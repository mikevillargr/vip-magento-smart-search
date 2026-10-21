"""Stage 2 via Claude Haiku: one structured-output call that picks year/vehicle/part from the candidates."""
from __future__ import annotations

import json
import os
import time

import anthropic

from ..catalog import Catalog
from ..normalize import Candidates
from ..types import InterpretResult, ParsedQuery

MODEL = os.environ.get("HAIKU_MODEL", "claude-haiku-5-5")
# $/1M tokens (Claude Haiku 5.5, prompts <100K). Cache reads ~0.1x, 5-min cache writes 1.25x.
PRICE_IN, PRICE_OUT = 0.10, 0.50
PRICE_CACHE_READ, PRICE_CACHE_WRITE = PRICE_IN * 0.1, PRICE_IN * 1.25

SCHEMA = {
    "type": "object",
    "properties": {
        "year": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "make": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "vehicles": {"type": "array", "items": {"type": "string"}},
        "part_types": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "string"},
        "sku": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidence": {"type": "number"},
        "most_relevant": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["year", "make", "vehicles", "part_types", "keywords", "sku", "confidence", "most_relevant"],
    "additionalProperties": False,
}


def build_system(cat: Catalog) -> str:
    vehicles = "\n".join(f"{mk} {md}: {cat.year_span(mk, md)}" for mk, md in sorted(cat.models))
    return f"""You interpret search queries typed into an auto-parts store's search box and map them to structured fields. Shoppers often type the vehicle into the search box instead of using the Year/Make/Model picker, e.g. "99 ford bronco oil filter".

Fields:
- year: the model year. Two-digit years: 50-99 mean 19xx, 00-49 mean 20xx ("'15" = 2015). null if none.
- make: the vehicle make if stated or implied by the model (e.g. "f150" implies Ford), else null.
- vehicles: vehicles from the VEHICLES list, written exactly as "<Make> <Model>" from that list. Shoppers use shorthand ("jl" = Wrangler JL, "f250" = Super Duty, "ram 1500" = 1500 DS or 1500 DT). When a model name spans generations and a year is given, pick the generation whose years include it. If still ambiguous, list every plausible vehicle. If the shopper's year is outside the listed years, still return the vehicle; the store explains the gap. Empty list if no specific model is mentioned.
- part_types: zero or more items copied exactly from the PART TYPE OPTIONS given with the query, covering what the shopper wants. Empty if the query is too vague or nothing fits.
- keywords: the product words useful for text search, spelling-corrected, without year/make/model words.
- sku: a part number if the query contains one, else null.
- confidence: 0-1, how sure you are of the overall interpretation.
- most_relevant: indices from CANDIDATE PRODUCTS that are what the shopper wants, most relevant first. Leave out products that don't match. Empty if no candidates are given.

VEHICLES (make model: years with fitment data):
{vehicles}"""


class HaikuInterpreter:
    name = "haiku"

    def __init__(self, cat: Catalog):
        self.cat = cat
        self.system = build_system(cat)
        self.vehicle_lookup = {f"{mk} {md}".lower(): (mk, md) for mk, md in cat.models}
        self.client = anthropic.AsyncAnthropic(timeout=15.0, max_retries=1) if os.environ.get("ANTHROPIC_API_KEY") else None
        self.use_cache = True

    def user_message(self, c: Candidates, products: list[dict] | None = None) -> str:
        hints = {
            "query": c.query,
            "pre_parser_hints": {
                "year_candidates": c.years,
                "vehicle_candidates": [f"{mk} {md}" for mk, md in c.vehicles],
                "part_number_candidates": c.skus,
            },
            "PART TYPE OPTIONS": c.parts,
            "CANDIDATE PRODUCTS": [{"i": i, "name": p["name"], "part_type": p["partname"]} for i, p in enumerate(products or [])],
        }
        return json.dumps(hints)

    async def interpret(self, c: Candidates, products: list[dict] | None = None) -> InterpretResult:
        if not self.client:
            return InterpretResult(ParsedQuery(), self.name, error="ANTHROPIC_API_KEY missing")
        system = [{"type": "text", "text": self.system}]
        if self.use_cache:
            system[0]["cache_control"] = {"type": "ephemeral"}
        t0 = time.perf_counter()
        try:
            resp = await self.client.messages.create(
                model=MODEL,
                max_tokens=400,
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                system=system,
                messages=[{"role": "user", "content": self.user_message(c, products)}],
            )
        except anthropic.RateLimitError as e:
            return InterpretResult(ParsedQuery(), self.name, network_ms=_ms(t0), error=f"rate limited: {e}")
        except anthropic.APIStatusError as e:
            return InterpretResult(ParsedQuery(), self.name, network_ms=_ms(t0), error=f"{e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            return InterpretResult(ParsedQuery(), self.name, network_ms=_ms(t0), error=f"connection: {e}")
        network_ms = _ms(t0)

        if resp.stop_reason == "refusal":
            return InterpretResult(ParsedQuery(), self.name, network_ms=network_ms, error="refusal")
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        data = json.loads(text)
        u = resp.usage
        cache_read = u.cache_read_input_tokens or 0
        cache_write = u.cache_creation_input_tokens or 0
        cost = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT
                + cache_read * PRICE_CACHE_READ + cache_write * PRICE_CACHE_WRITE) / 1e6
        return InterpretResult(
            parsed=self.to_parsed(data, c),
            engine=self.name,
            network_ms=network_ms,
            input_tokens=u.input_tokens + cache_read + cache_write,
            output_tokens=u.output_tokens,
            cached_tokens=cache_read,
            cost_usd=cost,
            request_id=getattr(resp, "_request_id", None),
            raw=data,
            relevance=self.relevance(data, products or []),
        )

    @staticmethod
    def relevance(d: dict, products: list[dict]) -> dict[str, float]:
        """Ordered list -> 0..1 scores: listed items 1.0 down to 0.5 by position, unlisted 0.1."""
        order = [i for i in d.get("most_relevant", []) if isinstance(i, int) and 0 <= i < len(products)]
        order = list(dict.fromkeys(order))
        scores = {p["sku"]: 0.1 for p in products}
        for k, i in enumerate(order):
            scores[products[i]["sku"]] = 1.0 - 0.5 * k / max(len(order), 1)
        return scores

    def to_parsed(self, d: dict, c: Candidates) -> ParsedQuery:
        vehicles = [self.vehicle_lookup[v.lower()] for v in d.get("vehicles", []) if v.lower() in self.vehicle_lookup]
        make = d.get("make")
        if not vehicles and make:
            vehicles = [v for v in self.cat.models if v[0].lower() == make.lower()]
        parts = [p for p in d.get("part_types", []) if p in self.cat.partnames]
        sku = d.get("sku")
        if sku and not self.cat.has_sku(sku):
            sku = None
        conf = d.get("confidence")
        return ParsedQuery(
            year=d.get("year"), vehicles=vehicles, partnames=parts,
            keywords=d.get("keywords", "") or "", sku=self.cat.canonical_sku(sku) if sku else None,
            confidence=float(conf) if conf is not None else None,
        )


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)

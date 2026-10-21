"""Stage 2 via TypeSafe Jev: one system_one call with parallel Choice/Noul questions.

Jev doesn't generate text - it picks among options code supplies (max 255 per Choice) and returns
calibrated probabilities. So code proposes candidates, Jev chooses, and keywords stay code-derived.
"""
from __future__ import annotations

import os
import time

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, TypeSafeAPIError, TypeSafeError

from ..catalog import Catalog
from ..normalize import Candidates
from ..types import InterpretResult, ParsedQuery

MODEL = os.environ.get("JEV_MODEL", "jev-latest")
# $/1M tokens: $42 per *billion* input tokens; output tokens are free.
PRICE_IN = float(os.environ.get("JEV_PRICE_IN_PER_M", "0.042"))
PRICE_OUT = float(os.environ.get("JEV_PRICE_OUT_PER_M", "0"))
NONE = "none"
SECONDARY = 0.5
MAKE_MIN_CONF = 0.6
PART_OPTIONS = 15  # part types checked one by one (yes/no each) - a search can want several ("floor mats")
PART_MIN = 0.5
PART_WINDOW = 0.3  # keep part types within this of the best score (drops loose extras like "Load Floor")  # keep extra options whose probability is >= 50% of the winner's (real ambiguity)


class JevInterpreter:
    name = "jev"

    def __init__(self, cat: Catalog, norm=None):
        self.cat = cat
        self.norm = norm  # rebuilds keywords after Jev picks the vehicle (Jev can't write text)
        self.client = None
        self.init_error = None
        self.extra_questions = True  # bench toggle: 5 questions (default) vs the 3 core ones
        if os.environ.get("TYPESAFE_API_KEY"):
            try:
                self.client = AsyncTypeSafeClient(model=MODEL, timeout=10.0)
            except TypeSafeError as e:
                self.init_error = str(e)
        self.vehicle_opts = {f"{mk} {md}": (mk, md) for mk, md in cat.models}
        self.makes = sorted({mk for mk, _ in cat.models})
        self.parts_count = {(r["make"], r["model"]): r["n"] for r in cat.con.execute("SELECT make, model, n FROM vocab_models")}

    def questions(self, c: Candidates, products: list[dict] | None = None) -> dict:
        q: dict = {}
        # Speculative fan-out: score the likely products in the same call. Questions run in parallel,
        # so this adds tokens but little time.
        for i, p in enumerate(products or []):
            q[f"rel{i}"] = Noul(instructions=f"The product '{p['name']}' (part type: {p['partname'] or 'n/a'}) "
                                             f"is what the shopper is searching for.")
        if c.years:
            q["year"] = Choice(
                instructions="Which model year of the vehicle is the shopper searching for?",
                criteria={str(y): None for y in dict.fromkeys(c.years)} | {NONE: "No vehicle model year is stated."},
            )
        # Only plausible vehicles: listing all 182 when none was typed made each call ~6x more expensive
        # (~5,600 vs ~870 tokens), and early type-ahead lookups ("2017", "2017 ford") hit that case constantly.
        pool = c.vehicles_wide or c.vehicles or [v for v in self.cat.models if v[0] in c.makes]
        # Year arithmetic stays in code (TypeSafe: Jev reads dates as text): drop generations that don't cover
        # the typed year, e.g. '10 ram 1500 -> 1500 DS only. Keep everything if none cover it (1999 Bronco).
        if c.years:
            covering = [v for v in pool if any(y in self.cat.models.get(v, []) for y in c.years)]
            pool = covering or pool
        if pool:
            q["vehicle"] = Choice(
                instructions="Which vehicle (make and model) is the shopper shopping for? Shoppers use shorthand like "
                             "'jl' for Wrangler JL or 'f250' for Super Duty.",
                criteria={f"{mk} {md}": f"model years {self.cat.year_span(mk, md)}; {self.parts_count[(mk, md)]:,} parts in catalog"
                          for mk, md in pool}
                | {NONE: "No specific vehicle model is mentioned."},
            )
        # Part type is multi-answer ("floor mats" = All Weather *and* Carpeted), so one yes/no per option rather
        # than a single Choice, which can only return one and tended to pick the literal "Mat".
        for i, p in enumerate(c.parts[:PART_OPTIONS]):
            q[f"pt{i}"] = Noul(instructions=f"The shopper is looking for this type of part: {p}.")
        if self.extra_questions:
            q["make"] = Choice(
                instructions="Which vehicle make is the shopper's vehicle?",
                criteria={m: None for m in self.makes} | {NONE: "No make is stated or implied."},
            )
            q["part_number"] = Noul(instructions="The query contains a manufacturer part number or SKU to look up.")
        return q

    async def interpret(self, c: Candidates, products: list[dict] | None = None) -> InterpretResult:
        if not self.client:
            return InterpretResult(ParsedQuery(), self.name, error=self.init_error or "TYPESAFE_API_KEY missing")
        t0 = time.perf_counter()
        try:
            resp = await self.client.system_one(
                state={"store": "auto parts e-commerce search box", "search_query": c.query},
                questions=self.questions(c, products),
            )
        except TypeSafeAPIError as e:
            return InterpretResult(ParsedQuery(), self.name, network_ms=_ms(t0), error=f"{e.status}: {e}")
        except TypeSafeError as e:
            return InterpretResult(ParsedQuery(), self.name, network_ms=_ms(t0), error=str(e))
        network_ms = _ms(t0)

        ch = resp.choices
        fc: dict[str, float] = {}
        raw: dict = {}

        year = None
        if "year" in ch:
            a = ch["year"]
            fc["year"] = a.confidence
            raw["year"] = {"choice": a.choice, "confidence": a.confidence}
            year = int(a.choice) if a.choice != NONE else None

        vehicles: list[tuple[str, str]] = []
        if "vehicle" in ch:
            a = ch["vehicle"]
            fc["vehicle"] = a.confidence
            picked = _top_options(a.probabilities)
            raw["vehicle"] = {"choice": a.choice, "confidence": a.confidence, "kept": picked}
            vehicles = [self.vehicle_opts[o] for o in picked if o in self.vehicle_opts]

        sku_query = bool(c.skus) and resp.nouls.get("part_number") is not None and resp.nouls["part_number"].noul > 0.5
        if "make" in ch:
            m = ch["make"]
            raw["make"] = {"choice": m.choice, "confidence": m.confidence}
            # A make guess only counts when confident and the shopper isn't looking up a part number.
            if not vehicles and m.choice != NONE and m.confidence >= MAKE_MIN_CONF and not sku_query:
                fc["make"] = m.confidence
                vehicles = [v for v in self.cat.models if v[0] == m.choice]

        part_scores = {p: resp.nouls[f"pt{i}"].noul for i, p in enumerate(c.parts[:PART_OPTIONS]) if f"pt{i}" in resp.nouls}
        likely = _drop_broader([p for p, s in sorted(part_scores.items(), key=lambda kv: -kv[1]) if s >= PART_MIN])
        best = max((part_scores[p] for p in likely), default=0.0)
        parts = [p for p in likely if part_scores[p] >= best - PART_WINDOW][:4]
        if part_scores:
            fc["part"] = max(part_scores.values())
            raw["part"] = {"kept": parts, "scores": {p: round(s, 3) for p, s in sorted(part_scores.items(), key=lambda kv: -kv[1])[:5]}}

        sku = None
        if "part_number" in resp.nouls:
            pn = resp.nouls["part_number"].noul
            raw["part_number"] = pn
            if pn > 0.5 and c.skus:
                sku = c.skus[0]

        relevance = {p["sku"]: resp.nouls[f"rel{i}"].noul for i, p in enumerate(products or []) if f"rel{i}" in resp.nouls}
        usage = resp.usage
        return InterpretResult(
            relevance=relevance,
            parsed=ParsedQuery(
                year=year, vehicles=vehicles, partnames=parts, sku=sku,
                keywords=self.norm.keywords_for(c, vehicles) if self.norm else c.residual,
                confidence=round(min(fc.values()), 3) if fc else None, field_confidence=fc,
            ),
            engine=self.name,
            network_ms=network_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=(usage.input_tokens * PRICE_IN + usage.output_tokens * PRICE_OUT) / 1e6,
            request_id=resp.__dict__.get("_request_id"),  # .request_id raises when the header is absent
            raw=raw,
        )


def _drop_broader(parts: list[str]) -> list[str]:
    """'Plug' is broader than 'Spark Plug': if a more specific kept type contains every word of a generic one, drop
    the generic (it would pull in drain plugs, track mats, etc.)."""
    def words(p: str) -> set[str]:
        return {w.rstrip("s") for w in p.lower().split()}
    return [p for p in parts if not any(o != p and words(p) < words(o) for o in parts)]


def _top_options(probs: dict[str, float]) -> list[str]:
    if not probs:
        return []
    best = max(probs.values())
    keep = [o for o, pr in sorted(probs.items(), key=lambda kv: -kv[1]) if pr >= best * SECONDARY]
    top = keep[0]
    if top == NONE:
        return []
    return [o for o in keep if o != NONE][:4]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)

"""Stage 4 (optional): re-rank the top N results with the same engine that interpreted the query.

Haiku: one listwise call returning an ordering. Jev: one call with a Noul per candidate - Jev evaluates
questions in parallel, so 20 candidates cost roughly one round trip.
"""
from __future__ import annotations

import json
import time

import anthropic
from typesafe_sdk import Noul, TypeSafeError

from .interpreters import haiku as haiku_mod
from .interpreters import jev as jev_mod
from .types import ParsedQuery

TOP_N = 20

ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order": {"type": "array", "items": {"type": "integer"}}},
    "required": ["order"],
    "additionalProperties": False,
}


def _vehicle(p: ParsedQuery) -> str:
    v = ", ".join(f"{mk} {md}" for mk, md in p.vehicles[:3])
    return f"{p.year or ''} {v}".strip()


async def rerank_haiku(h: "haiku_mod.HaikuInterpreter", query: str, p: ParsedQuery, results: list[dict]) -> dict:
    head, tail = results[:TOP_N], results[TOP_N:]
    if not h.client or len(head) < 2:
        return {"results": results, "ms": 0.0, "cost_usd": 0.0, "error": None if h.client else "no key"}
    items = [{"i": i, "name": r["name"], "part_type": r["partname"], "fit": r["fit"]} for i, r in enumerate(head)]
    t0 = time.perf_counter()
    try:
        resp = await h.client.messages.create(
            model=haiku_mod.MODEL,
            max_tokens=300,
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ORDER_SCHEMA}},
            messages=[{
                "role": "user",
                "content": "Order these auto-parts search results from most to least relevant to the shopper. "
                           "Return every index once.\n"
                           + json.dumps({"query": query, "vehicle": _vehicle(p), "results": items}),
            }],
        )
    except anthropic.APIError as e:
        return {"results": results, "ms": _ms(t0), "cost_usd": 0.0, "error": str(e)}
    ms = _ms(t0)
    order = json.loads(next(b.text for b in resp.content if b.type == "text")).get("order", [])
    seen, out = set(), []
    for i in order:
        if isinstance(i, int) and 0 <= i < len(head) and i not in seen:
            seen.add(i); out.append(head[i])
    out += [r for i, r in enumerate(head) if i not in seen]
    u = resp.usage
    cost = (u.input_tokens * haiku_mod.PRICE_IN + u.output_tokens * haiku_mod.PRICE_OUT) / 1e6
    return {"results": out + tail, "ms": ms, "cost_usd": cost, "error": None}


async def rerank_jev(j: "jev_mod.JevInterpreter", query: str, p: ParsedQuery, results: list[dict]) -> dict:
    head, tail = results[:TOP_N], results[TOP_N:]
    if not j.client or len(head) < 2:
        return {"results": results, "ms": 0.0, "cost_usd": 0.0, "error": None if j.client else "no key"}
    questions = {
        f"r{i}": Noul(instructions=f"The product '{r['name']}' (part type: {r['partname'] or 'n/a'}) is what the "
                                   f"shopper is looking for.")
        for i, r in enumerate(head)
    }
    t0 = time.perf_counter()
    try:
        resp = await j.client.system_one(
            state={"search_query": query, "vehicle": _vehicle(p) or "not specified"}, questions=questions
        )
    except TypeSafeError as e:
        return {"results": results, "ms": _ms(t0), "cost_usd": 0.0, "error": str(e)}
    ms = _ms(t0)
    scores = {i: resp.nouls[f"r{i}"].noul for i in range(len(head)) if f"r{i}" in resp.nouls}
    out = [head[i] for i in sorted(range(len(head)), key=lambda i: -scores.get(i, 0.0))]
    for i, r in enumerate(head):
        r["rerank_score"] = round(scores.get(i, 0.0), 3)
    u = resp.usage
    cost = (u.input_tokens * jev_mod.PRICE_IN + u.output_tokens * jev_mod.PRICE_OUT) / 1e6
    return {"results": out + tail, "ms": ms, "cost_usd": cost, "error": None}


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)

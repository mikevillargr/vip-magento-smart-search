"""Offline check of both interpreters against mocked HTTP (no API keys needed).

Run: .venv/bin/python tests/test_mocked_apis.py
Verifies request shape (what we send) and response mapping (what we parse) for Jev and Haiku.
"""
import asyncio
import json
import sys
from pathlib import Path

import anthropic
import httpx2
from anthropic import DefaultAsyncHttpxClient
from typesafe_sdk import AsyncTypeSafeClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.catalog import Catalog  # noqa: E402
from app.interpreters.haiku import HaikuInterpreter  # noqa: E402
from app.interpreters.jev import JevInterpreter  # noqa: E402
from app.normalize import Normalizer  # noqa: E402
from app.rerank import rerank_haiku, rerank_jev  # noqa: E402
from app.retrieve import retrieve  # noqa: E402

cat = Catalog()
cands = Normalizer(cat).run("1999 bronco filter")
seen: dict = {}


def jev_handler(req):
    body = json.loads(req.content)
    seen["jev"] = body
    picks = {"year": "1999", "vehicle": "Ford Bronco", "part": "Oil Filter", "make": "Ford"}
    ans = {}
    for k, q in body["questions"].items():
        if q["type"] == "noul":
            # part-type questions: yes for the two filters, no otherwise
            text = q.get("instructions", "")
            yes = k.startswith("pt") and (text.endswith(": Oil Filter.") or text.endswith(": Engine Air Filter."))
            ans[k] = {"type": "noul", "noul": (0.9 if text.endswith(": Oil Filter.") else 0.8) if yes else 0.05}
            continue
        opts = list(q["criteria"])
        pick = picks[k] if picks.get(k) in opts else opts[0]
        if k == "part":  # two close options -> both should be kept
            probs = {o: 0.45 if o == "Oil Filter" else 0.35 if o == "Engine Air Filter" else 0.2 / (len(opts) - 2) for o in opts}
        else:
            probs = {o: 0.8 if o == pick else 0.2 / (len(opts) - 1) for o in opts}
        ans[k] = {"type": "choice", "choice": pick, "confidence": 0.8, "probabilities": probs}
    return httpx2.Response(200, json={"model": "jev-1.13.0", "answers": ans, "usage": {"input_tokens": 900, "output_tokens": 12}},
                           headers={"x-typesafe-request-id": "req_mock"})


def haiku_handler(req):
    seen["haiku"] = json.loads(req.content)
    out = {"year": 1999, "make": "Ford", "vehicles": ["Ford Bronco"], "part_types": ["Oil Filter", "Engine Air Filter"],
           "keywords": "filter", "sku": None, "confidence": 0.7}
    return httpx2.Response(200, json={
        "id": "msg_mock", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": json.dumps(out)}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 60, "cache_read_input_tokens": 3100, "cache_creation_input_tokens": 0},
    })


async def main():
    j = JevInterpreter(cat)
    j.client = AsyncTypeSafeClient(api_key="ts-mock", model="jev-latest", transport=httpx2.MockTransport(jev_handler))
    rj = await j.interpret(cands)
    q = seen["jev"]["questions"]
    assert {"year", "vehicle", "make", "part_number"} <= set(q) and any(k.startswith("pt") for k in q), q.keys()
    assert "none" in q["vehicle"]["criteria"] and len(q["vehicle"]["criteria"]) <= 255
    assert rj.error is None and rj.parsed.year == 1999 and rj.parsed.vehicles == [("Ford", "Bronco")]
    assert rj.parsed.partnames == ["Oil Filter", "Engine Air Filter"], rj.parsed.partnames
    assert rj.request_id == "req_mock"
    print("jev   ok:", rj.parsed, f"cost ${rj.cost_usd:.9f}")

    h = HaikuInterpreter(cat)
    h.client = anthropic.AsyncAnthropic(api_key="sk-mock", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(haiku_handler)))
    rh = await h.interpret(cands)
    b = seen["haiku"]
    assert b["model"] == "claude-haiku-5-5" and b["thinking"] == {"type": "disabled"}
    assert b["output_config"]["format"]["type"] == "json_schema" and b["output_config"]["effort"] == "low"
    assert b["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert rh.error is None and rh.parsed.vehicles == [("Ford", "Bronco")] and rh.cached_tokens == 3100
    print("haiku ok:", rh.parsed, f"cost ${rh.cost_usd:.7f}", f"(system prompt ≈{len(b['system'][0]['text']) // 4} tokens)")

    res = retrieve(cat, rj.parsed)
    assert res["notes"] and "1999" in res["notes"][0]
    print("retrieve ok:", [r["name"][:40] for r in res["results"][:3]])

    # Re-rankers: Jev scores each candidate with a Noul; Haiku returns an ordering.
    def jev_rr(req):
        qs = json.loads(req.content)["questions"]
        return httpx2.Response(200, json={"model": "jev", "usage": {"input_tokens": 50, "output_tokens": 5},
                                          "answers": {k: {"type": "noul", "noul": int(k[1:]) / 100} for k in qs}})
    j.client = AsyncTypeSafeClient(api_key="ts-mock", model="jev-latest", transport=httpx2.MockTransport(jev_rr))
    rr = await rerank_jev(j, "1999 bronco filter", rj.parsed, res["results"])
    assert rr["error"] is None and rr["results"][0]["sku"] == res["results"][min(19, len(res["results"]) - 1)]["sku"]

    def haiku_rr(req):
        n = len(json.loads(json.loads(req.content)["messages"][0]["content"].split("\n", 1)[1])["results"])
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": json.dumps({"order": list(range(n))[::-1]})}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 300, "output_tokens": 30}})
    h.client = anthropic.AsyncAnthropic(api_key="sk-mock", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(haiku_rr)))
    rr2 = await rerank_haiku(h, "1999 bronco filter", rh.parsed, res["results"])
    assert rr2["error"] is None and rr2["results"][0]["sku"] == res["results"][min(19, len(res["results"]) - 1)]["sku"]
    print("rerank ok: jev + haiku reorder top", min(20, len(res["results"])))


asyncio.run(main())

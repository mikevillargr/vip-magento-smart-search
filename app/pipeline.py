"""The full search pipeline with per-stage timing. Shared by the API, the eval and the speed bench."""
from __future__ import annotations

import time
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()

from .catalog import Catalog  # noqa: E402
from .interpreters.haiku import HaikuInterpreter  # noqa: E402
from .interpreters.jev import JevInterpreter  # noqa: E402
from .interpreters.rules import is_unambiguous, resolve  # noqa: E402
from .normalize import Normalizer, key  # noqa: E402
from .rerank import rerank_haiku, rerank_jev  # noqa: E402
from .retrieve import annotate_fit, baseline, retrieve  # noqa: E402
from .suggest import suggested_searches  # noqa: E402
from .types import InterpretResult, ParsedQuery  # noqa: E402

ENGINES = ("baseline", "rules", "haiku", "jev")
RANK_N = 10  # products the AI scores for relevance, in the same call as interpretation


class Pipeline:
    def __init__(self):
        self.cat = Catalog()
        self.norm = Normalizer(self.cat)
        self.haiku = HaikuInterpreter(self.cat)
        self.jev = JevInterpreter(self.cat, self.norm)
        self.cache: OrderedDict = OrderedDict()

    def status(self) -> dict:
        return {
            "haiku": "ready" if self.haiku.client else "ANTHROPIC_API_KEY missing",
            "jev": "ready" if self.jev.client else (self.jev.init_error or "TYPESAFE_API_KEY missing"),
        }

    async def search(self, q: str, engine: str, *, fastpath: bool = False, rerank: bool = False,
                     use_cache: bool = True, ymm: tuple[int | None, str, str] | None = None, limit: int = 24,
                     rank: bool = True) -> dict:
        ck = (engine, key(q), fastpath, rerank, ymm, limit, rank)
        if use_cache and ck in self.cache:
            out = dict(self.cache[ck]); out["cached"] = True
            return out

        t_start = time.perf_counter()
        timings = {"normalize": 0.0, "prefetch": 0.0, "interpret_network": 0.0, "retrieve": 0.0, "rerank": 0.0}

        t = time.perf_counter()
        cands = self.norm.run(q)
        rules_parsed = resolve(cands, self.cat, self.norm)
        timings["normalize"] = _ms(t)

        def native() -> dict:
            t = time.perf_counter()
            res = baseline(self.cat, q, limit)
            # Badge native results against the rules parse so their fitment misses are visible.
            annotate_fit(self.cat, res["results"], rules_parsed.vehicles, rules_parsed.year)
            timings["retrieve"] = _ms(t)
            return res

        fallback = None
        if engine == "baseline":
            res = native()
            ir = InterpretResult(parsed=ParsedQuery(keywords=q), engine="baseline")
        else:
            if engine == "rules" or (fastpath and is_unambiguous(cands, rules_parsed)):
                ir = InterpretResult(parsed=rules_parsed, engine=engine, fast_path=engine != "rules")
            else:
                interp = self.haiku if engine == "haiku" else self.jev
                # Likely products from the code parse, so the AI can score relevance in the same call.
                t = time.perf_counter()
                prefetch = retrieve(self.cat, rules_parsed, RANK_N)["results"] if rank else []
                timings["prefetch"] = _ms(t)
                ir = await interp.interpret(cands, prefetch)
                timings["interpret_network"] = ir.network_ms
        if engine != "baseline" and ir.error:
            # Model failed (no key, timeout, 429, 5xx, refusal): fall back to native search, never to the other model.
            fallback = "native"
            ir.parsed = ParsedQuery(keywords=q)
            res = native()
        elif engine != "baseline":
            parsed = ir.parsed
            if ymm:  # explicit picker selection beats the text interpretation
                parsed.year, parsed.vehicles = ymm[0], [(ymm[1], ymm[2])]
            t = time.perf_counter()
            res = retrieve(self.cat, parsed, limit)
            if ir.relevance:
                # Re-order the first page only (most relevant first); products the AI didn't score keep a
                # neutral 0.5. Never pulls lower-ranked products onto the first page.
                head, tail = res["results"][:RANK_N], res["results"][RANK_N:]
                res["results"] = sorted(head, key=lambda r: -ir.relevance.get(r["sku"], 0.5)) + tail
                for r in res["results"]:
                    if r["sku"] in ir.relevance:
                        r["relevance"] = round(ir.relevance[r["sku"]], 3)
            timings["retrieve"] = _ms(t)

            if rerank and engine in ("haiku", "jev") and res["results"]:
                rr = await (rerank_haiku(self.haiku, q, parsed, res["results"]) if engine == "haiku"
                            else rerank_jev(self.jev, q, parsed, res["results"]))
                res["results"] = rr["results"]
                timings["rerank"] = rr["ms"]
                ir.cost_usd += rr["cost_usd"]

        timings["total"] = _ms(t_start)
        timings["overhead"] = round(timings["total"] - sum(v for k, v in timings.items() if k != "total"), 1)
        out = {
            "engine": engine,
            "query": q,
            "parsed": ir.parsed.to_dict(),
            "fast_path": ir.fast_path,
            "fast_path_eligible": is_unambiguous(cands, rules_parsed),
            "error": ir.error,
            "fallback": fallback,
            "timings_ms": timings,
            "usage": {"input_tokens": ir.input_tokens, "output_tokens": ir.output_tokens,
                      "cached_tokens": ir.cached_tokens, "cost_usd": round(ir.cost_usd, 8)},
            "request_id": ir.request_id,
            "raw": ir.raw,
            "candidates": {"years": cands.years, "vehicles": [list(v) for v in cands.vehicles[:10]],
                           "parts": cands.parts[:10], "residual": cands.residual},
            **res,
            "cached": False,
        }
        if use_cache and not ir.error:
            self.cache[ck] = out
            if len(self.cache) > 500:
                self.cache.popitem(last=False)
        return out

    async def suggest(self, q: str, engine: str, *, fastpath: bool = False, use_cache: bool = True,
                      ymm: tuple[int | None, str, str] | None = None, rank: bool = True) -> dict:
        """Type-ahead: same interpretation as a full search, up to 5 products, plus suggested searches for AI engines.
        AI engines only show products that fit the vehicle, labelled "Fits 2022 Ford Bronco"."""
        out = dict(await self.search(q, engine, fastpath=fastpath, use_cache=use_cache, ymm=ymm, limit=12, rank=rank))
        if engine == "baseline" or out.get("fallback"):
            out["suggestions"] = []
            out["results"] = out["results"][:5]
            return out
        t = time.perf_counter()
        parsed = ParsedQuery(year=out["parsed"]["year"], vehicles=[tuple(v) for v in out["parsed"]["vehicles"]],
                             partnames=out["parsed"]["partnames"])
        if parsed.vehicles:
            year = parsed.year if parsed.year is not None and any(
                parsed.year in self.cat.models.get(v, []) for v in parsed.vehicles) else None
            kept = []
            for r in out["results"]:
                if r["fit"] == "universal":
                    kept.append({**r, "fit_label": "Fits all vehicles"})
                elif r["fit"] == "fits":
                    v = next((v for v in parsed.vehicles if self.cat.fits(r["sku"], year, *v)), parsed.vehicles[0])
                    kept.append({**r, "fit_label": f"Fits {str(year) + ' ' if year else ''}{v[0]} {v[1]}"})
            out["results"] = kept
        out["results"] = out["results"][:5]
        out["suggestions"] = suggested_searches(self.cat, parsed)
        out["timings_ms"] = dict(out["timings_ms"])
        out["timings_ms"]["suggest"] = _ms(t)
        out["timings_ms"]["total"] = round(out["timings_ms"]["total"] + out["timings_ms"]["suggest"], 2)
        return out


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)

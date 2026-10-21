"""Relevance + parse-accuracy eval across engines.

Usage: .venv/bin/python eval/run_eval.py [--engines baseline,rules,haiku,jev] [--n 300] [--concurrency 8] [--fastpath]
Writes eval/report.md and eval/results.csv. Grading uses the fitment table and partnames, never an engine's own labels.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline import Pipeline  # noqa: E402

GOLDENS = ROOT / "eval" / "goldens.jsonl"


def expected_vehicles(g: dict, cat) -> list[tuple[str, str]]:
    v = g["vehicles"]
    if isinstance(v, str) and v.startswith("make:"):
        return [m for m in cat.models if m[0] == v[5:]]
    return [tuple(x) for x in v]


def grade(g: dict, out: dict, cat) -> dict:
    p = out["parsed"]
    exp_v = expected_vehicles(g, cat)
    got_v = [tuple(x) for x in p.get("vehicles", [])]
    make_only = isinstance(g["vehicles"], str)

    year_ok = p.get("year") == g["year"]
    if make_only:
        veh_ok = bool(got_v) and {m for m, _ in got_v} == {g["vehicles"][5:]}
    elif not exp_v:
        veh_ok = not got_v
    else:
        veh_ok = bool(got_v) and set(got_v) <= set(exp_v) | {v for v in got_v if v in exp_v} and any(v in exp_v for v in got_v) and len(got_v) <= 4
    exp_parts = set(g.get("parts", []))
    got_parts = set(p.get("partnames", []))
    part_ok = (bool(got_parts & exp_parts)) if exp_parts else True
    sku_ok = (p.get("sku") or "").upper() == g["sku"].upper() if g.get("sku") else True

    # Relevance of the top 10, judged against the *expected* vehicle/year.
    top = out["results"][:10]
    if exp_v:
        fit_year = g["year"] if g["year"] and any(g["year"] in cat.models.get(v, []) for v in exp_v) else None
        fits = cat.fitting_skus(exp_v, fit_year) | cat.universal_skus()
        fit_flags = [r["sku"] in fits for r in top]
    else:
        fit_flags = [True] * len(top)
    part_flags = [(r["partname"] in exp_parts) if exp_parts else True for r in top]
    if g.get("sku"):
        part_flags = [r["sku"].upper() == g["sku"].upper() or f for r, f in zip(top, part_flags)]
    joint = [a and b for a, b in zip(fit_flags, part_flags)]
    n = len(top) or 1
    return {
        "year_ok": year_ok, "vehicle_ok": veh_ok, "part_ok": part_ok, "sku_ok": sku_ok,
        "parse_ok": year_ok and veh_ok and part_ok and sku_ok,
        "fit_p10": sum(fit_flags) / n if top else 0.0,
        "part_p10": sum(part_flags) / n if top else 0.0,
        "joint_p10": sum(joint) / n if top else 0.0,
        "success_at_3": any(joint[:3]),
        "top1": bool(joint[:1] and joint[0]),
        "zero_results": not top,
    }


async def run_engine(pipe: Pipeline, engine: str, goldens: list[dict], conc: int, fastpath: bool,
                     typeahead: bool = True, rank: bool = True) -> list[dict]:
    sem = asyncio.Semaphore(conc)

    async def one(g):
        async with sem:
            out = await pipe.search(g["query"], engine, fastpath=fastpath, use_cache=False, rank=rank)
            # Type-ahead, measured for real: one suggestion lookup per word as the shopper types. The last lookup
            # is the full query, which the final search reuses, so a 4-word search = 3 lookups + 1 search.
            ta_cost, ta_calls, ta_ms, ta_tok = 0.0, 0, [], 0
            ta_cached, ta_out = 0, 0
            if typeahead and engine in ("haiku", "jev"):
                words = g["query"].split()
                for i in range(1, len(words)):
                    s = await pipe.suggest(" ".join(words[:i]), engine, fastpath=fastpath, use_cache=False, rank=rank)
                    ta_cost += s["usage"]["cost_usd"]
                    ta_tok += s["usage"]["input_tokens"]
                    ta_cached += s["usage"]["cached_tokens"]
                    ta_out += s["usage"]["output_tokens"]
                    ta_calls += 0 if (s["fast_path"] or s["error"]) else 1
                    ta_ms.append(s["timings_ms"]["total"])
        return {"typeahead_calls": ta_calls, "typeahead_cost": ta_cost,
                "typeahead_ms": statistics.median(ta_ms) if ta_ms else float("nan"),
                "search_cost": out["usage"]["cost_usd"] + ta_cost,
                "search_tokens": out["usage"]["input_tokens"] + ta_tok,
                "search_cached_tokens": out["usage"]["cached_tokens"] + ta_cached,
                "search_output_tokens": out["usage"]["output_tokens"] + ta_out,
                "id": g["id"], "type": g["type"], "query": g["query"], "engine": engine,
                "error": out["error"], "fast_path": out["fast_path"], "confidence": out["parsed"].get("confidence"),
                "parsed": json.dumps(out["parsed"]), "top3": " | ".join(r["name"][:50] for r in out["results"][:3]),
                **{f"t_{k}": v for k, v in out["timings_ms"].items()}, "cost_usd": out["usage"]["cost_usd"],
                **grade(g, out, pipe.cat)}

    return await asyncio.gather(*(one(g) for g in goldens))


def pctl(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["error"]]
    mean = lambda k: statistics.mean(float(r[k]) for r in ok) if ok else float("nan")  # noqa: E731
    return {
        "n": len(rows), "errors": len(rows) - len(ok),
        "parse_acc": mean("parse_ok"), "year_acc": mean("year_ok"), "vehicle_acc": mean("vehicle_ok"),
        "part_acc": mean("part_ok"), "fit_p10": mean("fit_p10"), "part_p10": mean("part_p10"),
        "joint_p10": mean("joint_p10"), "success_at_3": mean("success_at_3"), "top1": mean("top1"), "zero_rate": mean("zero_results"),
        "interp_p50": pctl([r["t_interpret_network"] for r in ok if r["t_interpret_network"]], 50),
        "interp_p95": pctl([r["t_interpret_network"] for r in ok if r["t_interpret_network"]], 95),
        "total_p50": pctl([r["t_total"] for r in ok], 50), "total_p95": pctl([r["t_total"] for r in ok], 95),
        "cost_per_1k": 1000 * statistics.mean(r["cost_usd"] for r in ok) if ok else float("nan"),
        "search_cost_per_1k": 1000 * statistics.mean(r["search_cost"] for r in ok) if ok else float("nan"),
        "tokens_per_search": statistics.mean(r["search_tokens"] for r in ok) if ok else float("nan"),
        "cached_per_search": statistics.mean(r["search_cached_tokens"] for r in ok) if ok else float("nan"),
        "output_per_search": statistics.mean(r["search_output_tokens"] for r in ok) if ok else float("nan"),
        "calls_per_search": statistics.mean((0 if r["fast_path"] else 1) + r["typeahead_calls"] for r in ok) if ok else float("nan"),
        "typeahead_calls": statistics.mean(r["typeahead_calls"] for r in ok) if ok else float("nan"),
        "typeahead_p50": pctl([r["typeahead_ms"] for r in ok if r["typeahead_ms"] == r["typeahead_ms"]], 50),
        "fast_path_rate": statistics.mean(1.0 if r["fast_path"] else 0.0 for r in rows),
    }


def calibration(rows: list[dict]) -> list[tuple[str, int, float]]:
    buckets = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01)]
    out = []
    for lo, hi in buckets:
        rs = [r for r in rows if r["confidence"] is not None and lo <= r["confidence"] < hi and not r["error"]]
        if rs:
            out.append((f"{lo:.2f}–{min(hi, 1):.2f}", len(rs), statistics.mean(1.0 if r["parse_ok"] else 0.0 for r in rs)))
    return out


NAMES = {"baseline": "Today's site search", "rules": "Rules only (no AI)", "haiku": "Claude Haiku", "jev": "TypeSafe Jev"}


def typeahead_calls(query: str) -> int:
    """Assumption: the shopper pauses once per word while typing, so the dropdown asks once per word."""
    return len(query.split())


def plain_summary(summaries: dict, rows: list[dict], goldens: list[dict], monthly: int) -> list[str]:
    """Stakeholder view: accuracy, speed, cost. No statistics jargon. Costs are measured, not estimated."""
    words = statistics.mean(len(g["query"].split()) for g in goldens)
    L = ["## Summary", "",
         "**Accuracy** — of the parts shown on the first page, how many fit the shopper's vehicle *and* are the part they asked for. "
         "**Top result is right** — the very first part shown fits and is what they asked for. "
         "**Speed** — typical time until suggestions and results appear. "
         f"**Cost** — AI cost per 1,000 searches, measured by replaying each search as it's typed (a suggestion lookup per word, "
         f"about {words - 1:.1f} per search, plus the final search).", "",
         "| | Accuracy | Top result is right | Parts shown that don't fit the vehicle | Suggestions appear in | Results appear in | Cost per 1,000 searches | Cost per month* |",
         "|---|---|---|---|---|---|---|---|"]
    for e, s in summaries.items():
        cost = s["search_cost_per_1k"] if s["search_cost_per_1k"] == s["search_cost_per_1k"] else 0.0
        ta = secs(s["typeahead_p50"]) if s.get("typeahead_p50") == s.get("typeahead_p50") else ("same as results" if e in ("baseline", "rules") else "—")
        L.append(f"| **{NAMES.get(e, e)}** | {s['joint_p10'] * 100:.0f}% | {s['top1'] * 100:.0f}% | {(1 - s['fit_p10']) * 100:.0f}% | {ta} | {secs(s['total_p50'])} | "
                 f"{money(cost)} | {money(cost / 1000 * monthly)} |")
    L += ["", f"\\* at {monthly:,} searches a month.", ""]
    h, j, b = summaries.get("haiku"), summaries.get("jev"), summaries.get("baseline")
    bullets = []
    if b:
        bullets.append(f"Today's site search shows parts that don't fit the shopper's vehicle **{(1 - b['fit_p10']) * 100:.0f}%** of the time.")
    for e in ("jev", "haiku"):
        s = summaries.get(e)
        if s and b:
            bullets.append(f"{NAMES[e]} gets the right part for the right vehicle **{s['joint_p10'] * 100:.0f}%** of the time, "
                           f"vs {b['joint_p10'] * 100:.0f}% today.")
    if h and j:
        sp = h["total_p50"] / max(j["total_p50"], 1e-9)
        co = h["search_cost_per_1k"] / max(j["search_cost_per_1k"], 1e-12)
        bullets.append(f"TypeSafe Jev is **{ratio_words(sp, 'faster', 'slower')}** and **{ratio_words(co, 'cheaper', 'more expensive')}** than Claude Haiku.")
        bullets.append(f"Accuracy: Jev {j['joint_p10'] * 100:.0f}% vs Haiku {h['joint_p10'] * 100:.0f}%.")
    j_, h_ = summaries.get("jev"), summaries.get("haiku")
    if j_ and j_["tokens_per_search"] == j_["tokens_per_search"]:
        t = j_["tokens_per_search"]
        bullets.append(f"How TypeSafe Jev's cost adds up: {j_['calls_per_search']:.1f} AI calls per search ≈ {t:,.0f} input tokens "
                       f"→ {t * 1000 / 1e6:.2f} million per 1,000 searches × $0.042 per million = **{money(t * 1000 / 1e6 * 0.042)}**. "
                       f"Its ~{j_['output_per_search']:,.0f} output tokens per search are free.")
    if h_ and h_["tokens_per_search"] == h_["tokens_per_search"]:
        new_in = h_["tokens_per_search"] - h_["cached_per_search"]
        cached, out = h_["cached_per_search"], h_["output_per_search"]
        k = 1000 / 1e6  # per search -> millions per 1,000 searches
        c_new, c_cached, c_out = new_in * k * 0.10, cached * k * 0.01, out * k * 0.50
        bullets.append(f"How Claude Haiku's cost adds up, per 1,000 searches ({h_['calls_per_search']:.1f} AI calls each): "
                       f"{new_in * k:.2f}M new input tokens × $0.10 = {money(c_new)} · "
                       f"{cached * k:.2f}M cached input tokens (its reused instructions) × $0.01 = {money(c_cached)} · "
                       f"**{out * k:.2f}M output tokens × $0.50 = {money(c_out)}** → **{money(c_new + c_cached + c_out)}**. "
                       f"Output is {c_out / max(c_new + c_cached + c_out, 1e-12) * 100:.0f}% of Haiku's cost; Jev charges nothing for output.")
    r = summaries.get("rules")
    if r and (h or j):
        bullets.append(f"For comparison, simple rules with no AI at all reach {r['joint_p10'] * 100:.0f}% — the AI's value is the remaining gap "
                       "(typos, nicknames, unusual wording).")
    return L + [f"- {x}" for x in bullets]


def ratio_words(x: float, better: str, worse: str) -> str:
    if 1 / 1.15 < x < 1.15:
        return "about the same"
    return f"{x:.1f}× {better}" if x >= 1 else f"{1 / x:.1f}× {worse}"


def secs(ms: float) -> str:
    return "—" if ms != ms else f"{ms / 1000:.2f} s" if ms >= 100 else "under 0.1 s"


def money(v: float) -> str:
    return "$0" if not v else f"${v:,.2f}" if v >= 1 else f"${v:.3f}"


def fmt(v, kind="pct"):
    if v != v:  # NaN
        return "—"
    return f"{v * 100:.1f}%" if kind == "pct" else f"{v:.0f} ms" if kind == "ms" else f"${v:.4f}"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="baseline,rules,haiku,jev")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--fastpath", action="store_true", help="let haiku/jev skip unambiguous queries")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-rank", action="store_true", help="don't ask the AI to rank products by relevance")
    ap.add_argument("--no-typeahead", action="store_true", help="skip measuring suggestion lookups (faster, cheaper)")
    ap.add_argument("--monthly-searches", type=int, default=100_000, help="volume used for the monthly cost line")
    args = ap.parse_args()

    goldens = [json.loads(line) for line in GOLDENS.read_text().splitlines() if line.strip()]
    if args.n < len(goldens):  # representative sample: edge cases keep their natural share
        goldens = random.Random(args.seed).sample(goldens, args.n)
    pipe = Pipeline()
    engines = args.engines.split(",")
    status = pipe.status()
    for e in ("haiku", "jev"):
        if e in engines and status[e] != "ready":
            print(f"skipping {e}: {status[e]}")
            engines.remove(e)

    all_rows: list[dict] = []
    summaries = {}
    for e in engines:
        t0 = time.time()
        rows = await run_engine(pipe, e, goldens, args.concurrency, args.fastpath, not args.no_typeahead, not args.no_rank)
        all_rows += rows
        summaries[e] = summarize(rows)
        print(f"{e:9s} {len(rows)} queries in {time.time() - t0:.1f}s  parse={fmt(summaries[e]['parse_acc'])} "
              f"joint_p10={fmt(summaries[e]['joint_p10'])} errors={summaries[e]['errors']}")

    with open(ROOT / "eval" / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    lines = [f"# Search comparison — {len(goldens)} test searches", ""]
    lines += plain_summary(summaries, all_rows, goldens, args.monthly_searches)
    lines += ["", "---", "", "# Technical appendix", "",
             f"Engines: {', '.join(engines)} · fast-path: {'on' if args.fastpath else 'off'} · concurrency {args.concurrency}", "",
             "## Relevance (top 10, graded against fitment table + partnames)", "",
             "| Engine | Fit P@10 | Part P@10 | **Fit+Part P@10** | Success@3 | Zero results |",
             "|---|---|---|---|---|---|"]
    for e, s in summaries.items():
        lines.append(f"| {e} | {fmt(s['fit_p10'])} | {fmt(s['part_p10'])} | **{fmt(s['joint_p10'])}** | {fmt(s['success_at_3'])} | {fmt(s['zero_rate'])} |")
    lines += ["", "## Query understanding (parse accuracy)", "",
              "| Engine | All fields | Year | Vehicle | Part | Errors |", "|---|---|---|---|---|---|"]
    for e, s in summaries.items():
        if e == "baseline":
            continue
        lines.append(f"| {e} | {fmt(s['parse_acc'])} | {fmt(s['year_acc'])} | {fmt(s['vehicle_acc'])} | {fmt(s['part_acc'])} | {s['errors']} |")
    lines += ["", "## Speed & cost (in-process, uncached)", "",
              "| Engine | Interpret p50 | Interpret p95 | Total p50 | Total p95 | $ / 1k queries | Fast-path rate |",
              "|---|---|---|---|---|---|---|"]
    for e, s in summaries.items():
        lines.append(f"| {e} | {fmt(s['interp_p50'], 'ms')} | {fmt(s['interp_p95'], 'ms')} | {fmt(s['total_p50'], 'ms')} | "
                     f"{fmt(s['total_p95'], 'ms')} | {fmt(s['cost_per_1k'], 'usd')} | {fmt(s['fast_path_rate'])} |")
    lines += ["", "## Parse accuracy by query type", ""]
    types = sorted({r["type"] for r in all_rows})
    lines += ["| Type | n | " + " | ".join(e for e in engines if e != "baseline") + " |",
              "|---|---|" + "---|" * len([e for e in engines if e != "baseline"])]
    for t in types:
        cells = []
        for e in engines:
            if e == "baseline":
                continue
            rs = [r for r in all_rows if r["engine"] == e and r["type"] == t and not r["error"]]
            cells.append(fmt(statistics.mean(1.0 if r["parse_ok"] else 0.0 for r in rs)) if rs else "—")
        n = sum(1 for r in all_rows if r["engine"] == engines[0] and r["type"] == t)
        lines.append(f"| {t} | {n} | " + " | ".join(cells) + " |")
    for e in ("jev", "haiku"):
        if e in engines:
            cal = calibration([r for r in all_rows if r["engine"] == e])
            if cal:
                lines += ["", f"## {e} calibration — is reported confidence trustworthy?", "",
                          "| Confidence | n | Parse accuracy |", "|---|---|---|"]
                lines += [f"| {b} | {n} | {fmt(a)} |" for b, n, a in cal]
    misses = [r for r in all_rows if r["engine"] not in ("baseline",) and not r["parse_ok"]][:40]
    if misses:
        lines += ["", "## Sample parse misses", "", "| Engine | Type | Query | Parsed |", "|---|---|---|---|"]
        for r in misses:
            p = json.loads(r["parsed"])
            lines.append(f"| {r['engine']} | {r['type']} | `{r['query']}` | y={p['year']} v={p['vehicles'][:2]} pn={p['partnames'][:2]} |")
    (ROOT / "eval" / "report.md").write_text("\n".join(lines) + "\n")
    print(f"wrote eval/report.md and eval/results.csv")


if __name__ == "__main__":
    asyncio.run(main())

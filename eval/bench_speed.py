"""Speed benchmark: Haiku vs Jev (plus code-only engines for reference).

Measures, per engine:
  * network baseline - bare HTTPS round trip to each API host, cold (new connection) and warm (keep-alive)
  * cold vs warm     - first call on a brand-new client vs later calls on a warmed connection
  * sequential       - p50/p90/p95/p99/min/max per stage, one query at a time
  * concurrency      - throughput, tail latency and rate-limit errors at 1/5/10/25 concurrent users
  * payload          - Haiku with/without prompt caching; Jev with 5 vs 3 parallel questions
Output: eval/speed_report.md, eval/speed_raw.csv, eval/speed_hist.png

Usage: .venv/bin/python eval/bench_speed.py [--n 200] [--concurrency 1,5,10,25] [--engines haiku,jev]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.interpreters.haiku import HaikuInterpreter  # noqa: E402
from app.interpreters.jev import JevInterpreter  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402

HOSTS = {"haiku": "https://api.anthropic.com/", "jev": "https://api.typesafe.ai/"}
BUDGET_TOTAL_P95 = 300.0
BUDGET_INTERP_P95 = 200.0
STAGES = ("normalize", "interpret_network", "retrieve", "rerank", "total")


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stats(xs):
    return {"n": len(xs), "p50": pct(xs, 50), "p90": pct(xs, 90), "p95": pct(xs, 95), "p99": pct(xs, 99),
            "min": min(xs) if xs else float("nan"), "max": max(xs) if xs else float("nan"),
            "mean": statistics.mean(xs) if xs else float("nan")}


def ms(v):
    return "—" if v != v else f"{v:.0f}" if v >= 10 else f"{v:.1f}"


async def network_baseline(engines, n=10):
    """TCP+TLS+HTTP round trip with no model work: cold = new connection each time, warm = reused."""
    out = {}
    for e in engines:
        url = HOSTS[e]
        cold, warm = [], []
        for _ in range(3):
            async with httpx2.AsyncClient(timeout=10) as c:
                t = time.perf_counter()
                await c.get(url)
                cold.append((time.perf_counter() - t) * 1000)
        async with httpx2.AsyncClient(timeout=10) as c:
            await c.get(url)
            for _ in range(n):
                t = time.perf_counter()
                await c.get(url)
                warm.append((time.perf_counter() - t) * 1000)
        out[e] = {"cold": stats(cold), "warm": stats(warm)}
    return out


async def cold_vs_warm(pipe: Pipeline, engine: str, queries: list[str]) -> dict:
    """First call on a fresh client (connection setup + any server-side cold path) vs warmed calls."""
    fresh = HaikuInterpreter(pipe.cat) if engine == "haiku" else JevInterpreter(pipe.cat)
    c = pipe.norm.run(queries[0])
    r = await fresh.interpret(c)
    cold = r.network_ms
    warm = []
    for q in queries[1:11]:
        r = await fresh.interpret(pipe.norm.run(q))
        if not r.error:
            warm.append(r.network_ms)
    return {"cold": cold, "warm": stats(warm)}


async def sequential(pipe: Pipeline, engine: str, queries: list[str], **kw) -> list[dict]:
    rows = []
    for q in queries:
        t = time.perf_counter()
        out = await pipe.search(q, engine, use_cache=False, **kw)
        rows.append({"engine": engine, "mode": "sequential", "query": q, "error": out["error"],
                     "client_ms": (time.perf_counter() - t) * 1000, **out["timings_ms"],
                     "input_tokens": out["usage"]["input_tokens"], "cached_tokens": out["usage"]["cached_tokens"],
                     "cost_usd": out["usage"]["cost_usd"]})
    return rows


async def concurrent(pipe: Pipeline, engine: str, queries: list[str], conc: int) -> dict:
    sem = asyncio.Semaphore(conc)
    lat, errors, rate_limited = [], 0, 0

    async def one(q):
        nonlocal errors, rate_limited
        async with sem:
            out = await pipe.search(q, engine, use_cache=False)
        if out["error"]:
            errors += 1
            rate_limited += ("429" in out["error"] or "rate" in out["error"].lower())
        else:
            lat.append(out["timings_ms"]["total"])

    t = time.perf_counter()
    await asyncio.gather(*(one(q) for q in queries))
    wall = time.perf_counter() - t
    return {"concurrency": conc, "n": len(queries), "qps": len(queries) / wall, "errors": errors,
            "rate_limited": rate_limited, **{k: v for k, v in stats(lat).items() if k in ("p50", "p95", "p99", "max")}}


def histogram(seq_rows, engines, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    colors = {"haiku": "#c4622d", "jev": "#2f6fdb", "rules": "#4fa37a", "baseline": "#7a8594"}
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=130)
    data = {e: [r["interpret_network"] for r in seq_rows if r["engine"] == e and not r["error"] and r["interpret_network"]]
            for e in engines}
    data = {e: v for e, v in data.items() if v}
    if not data:
        plt.close(fig)
        return False
    hi = max(pct(v, 99) for v in data.values())
    bins = [i * hi / 40 for i in range(41)]
    for e, v in data.items():
        ax.hist(v, bins=bins, alpha=0.55, label=f"{e} (p50 {pct(v, 50):.0f} ms)", color=colors.get(e))
    ax.axvline(BUDGET_INTERP_P95, color="#c8102e", ls="--", lw=1)
    ax.text(BUDGET_INTERP_P95, ax.get_ylim()[1] * 0.95, " interpret budget", color="#c8102e", fontsize=8, va="top")
    ax.set_xlabel("interpret latency (ms, network wall clock)")
    ax.set_ylabel("queries")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--concurrency", default="1,5,10,25")
    ap.add_argument("--engines", default="haiku,jev")
    ap.add_argument("--skip-load", action="store_true", help="skip the concurrency test")
    args = ap.parse_args()

    goldens = [json.loads(line) for line in (ROOT / "eval" / "goldens.jsonl").read_text().splitlines() if line.strip()]
    queries = [g["query"] for g in goldens][: args.n]
    pipe = Pipeline()
    status = pipe.status()
    engines = [e for e in args.engines.split(",") if status.get(e, "ready") == "ready"]
    skipped = [e for e in args.engines.split(",") if e not in engines]
    for e in skipped:
        print(f"skipping {e}: {status.get(e)}")
    model_engines = [e for e in engines if e in HOSTS]

    # Fast-path eligibility doesn't depend on the engine.
    eligible = [(await pipe.search(q, "rules", use_cache=False))["fast_path_eligible"] for q in queries]
    fp_rate = sum(eligible) / len(eligible) if eligible else 0.0

    print("network baseline…")  # needs no key, so measure every requested API host
    net = await network_baseline([e for e in args.engines.split(",") if e in HOSTS])

    print("warm-up + cold vs warm…")
    cvw = {e: await cold_vs_warm(pipe, e, queries) for e in model_engines}

    seq_rows: list[dict] = []
    for e in engines + ["rules"]:
        print(f"sequential {e} ({len(queries)} queries)…")
        seq_rows += await sequential(pipe, e, queries)

    print("type-ahead…")
    typeahead: dict[str, list[float]] = {}
    for e in engines:
        typeahead[e] = []
        for q in queries[: min(30, len(queries))]:
            words = q.split()
            for i in range(1, len(words) + 1):  # one lookup per word, as a shopper pauses while typing
                out = await pipe.suggest(" ".join(words[:i]), e, use_cache=False)
                if not out["error"]:
                    typeahead[e].append(out["timings_ms"]["total"])

    print("payload sensitivity…")
    payload = {}
    sub = queries[: min(50, len(queries))]
    if "haiku" in model_engines:
        pipe.haiku.use_cache = False
        no_cache = await sequential(pipe, "haiku", sub)
        pipe.haiku.use_cache = True
        with_cache = [r for r in seq_rows if r["engine"] == "haiku"][: len(sub)]
        payload["haiku: prompt caching on"] = with_cache
        payload["haiku: prompt caching off"] = no_cache
    if "jev" in model_engines:
        pipe.jev.extra_questions = False
        three = await sequential(pipe, "jev", sub)
        pipe.jev.extra_questions = True
        payload["jev: 5 questions (default)"] = [r for r in seq_rows if r["engine"] == "jev"][: len(sub)]
        payload["jev: 3 questions"] = three

    load = {}
    if not args.skip_load:
        for e in model_engines:
            load[e] = []
            for c in [int(x) for x in args.concurrency.split(",")]:
                print(f"load {e} @ {c} concurrent…")
                load[e].append(await concurrent(pipe, e, queries[: max(c * 4, 40)], c))

    # ---- write outputs ----
    with open(ROOT / "eval" / "speed_raw.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(seq_rows[0].keys()))
        w.writeheader()
        w.writerows(seq_rows)
    has_png = histogram(seq_rows, engines, ROOT / "eval" / "speed_hist.png")

    names = {"baseline": "Today's site search", "rules": "Rules only (no AI)", "haiku": "Claude Haiku", "jev": "TypeSafe Jev"}
    sec = lambda v: "—" if v != v else ("under 0.1 s" if v < 100 else f"{v / 1000:.2f} s")  # noqa: E731
    L = [f"# Speed report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
         "## Summary", "",
         "How long shoppers wait. *Typical* is what most searches feel like; *slow* is the slowest 1 in 20.", "",
         "| | Suggestions appear in (typical) | Results appear in (typical) | Results appear in (slow) | Feels instant?* |",
         "|---|---|---|---|---|"]
    for e in engines + ["rules"]:
        rs = [r["total"] for r in seq_rows if r["engine"] == e and not r["error"]]
        ta = typeahead.get(e, [])
        slow = pct(rs, 95)
        L.append(f"| **{names.get(e, e)}** | {sec(pct(ta, 50)) if ta else '—'} | {sec(pct(rs, 50))} | {sec(slow)} | "
                 f"{'✅ yes' if slow <= BUDGET_TOTAL_P95 else '❌ no'} |")
    L += ["", f"\\* under {BUDGET_TOTAL_P95 / 1000:.1f} s even for slow searches.", ""]
    if "haiku" in engines and "jev" in engines:
        hs = pct([r["total"] for r in seq_rows if r["engine"] == "haiku" and not r["error"]], 50)
        js = pct([r["total"] for r in seq_rows if r["engine"] == "jev" and not r["error"]], 50)
        L.append(f"- TypeSafe Jev shows results **{hs / js:.1f}× faster** than Claude Haiku.")
    if net:
        n = statistics.mean(d["warm"]["p50"] for d in net.values())
        L.append(f"- About **{sec(n)}** of every AI call is just the internet round trip from this machine to the AI provider. "
                 "Hosting the search service near the providers would cut that.")
    L.append(f"- {fp_rate * 100:.0f}% of test searches are simple enough to answer without AI (instant and free).")
    L += ["", "---", "", "# Technical appendix", "",
         f"Machine: {platform.node()} · {platform.platform()} · Python {platform.python_version()}. "
         f"Latency to the APIs depends on where this runs; compare with the network baseline below.", "",
         f"Queries: {len(queries)} from eval/goldens.jsonl · cache disabled · in-process timing "
         f"(`time.perf_counter`), so numbers exclude browser↔server HTTP.", ""]
    if skipped:
        L += [f"> Skipped (no key): {', '.join(skipped)}", ""]

    L += ["## Verdict vs latency budget", "",
          f"Budget: end-to-end p95 ≤ {BUDGET_TOTAL_P95:.0f} ms, interpret p95 ≤ {BUDGET_INTERP_P95:.0f} ms. "
          f"Fast-path-eligible queries (no model call needed): **{fp_rate * 100:.0f}%**.", "",
          "| Engine | Interpret p95 | Total p95 | Verdict |", "|---|---|---|---|"]
    for e in engines + ["rules"]:
        rs = [r for r in seq_rows if r["engine"] == e and not r["error"]]
        ip95 = pct([r["interpret_network"] for r in rs if r["interpret_network"]], 95)
        tp95 = pct([r["total"] for r in rs], 95)
        ok = tp95 <= BUDGET_TOTAL_P95 and (ip95 != ip95 or ip95 <= BUDGET_INTERP_P95)
        L.append(f"| {e} | {ms(ip95)} ms | {ms(tp95)} ms | {'✅ pass' if ok else '❌ over budget'} |")

    if net:
        L += ["", "## Network baseline (no model work)", "",
              "Bare HTTPS GET to the API host. Subtract *warm* from interpret latency to estimate model + queueing time.", "",
              "| Host | Cold p50 (new TLS) | Warm p50 | Warm p95 |", "|---|---|---|---|"]
        for e, d in net.items():
            L.append(f"| {HOSTS[e]} | {ms(d['cold']['p50'])} ms | {ms(d['warm']['p50'])} ms | {ms(d['warm']['p95'])} ms |")

    if cvw:
        L += ["", "## Cold vs warm client", "", "| Engine | First call (cold) | Warm p50 | Warm p95 |", "|---|---|---|---|"]
        for e, d in cvw.items():
            L.append(f"| {e} | {ms(d['cold'])} ms | {ms(d['warm']['p50'])} ms | {ms(d['warm']['p95'])} ms |")

    L += ["", "## Sequential latency by stage (ms)", ""]
    for e in engines + ["rules"]:
        rs = [r for r in seq_rows if r["engine"] == e and not r["error"]]
        errs = sum(1 for r in seq_rows if r["engine"] == e and r["error"])
        L += [f"### {e} — {len(rs)} ok, {errs} errors", "", "| Stage | p50 | p90 | p95 | p99 | min | max |", "|---|---|---|---|---|---|---|"]
        for st in STAGES:
            xs = [r[st] for r in rs if r[st] or st == "total"]
            if not xs:
                continue
            s = stats(xs)
            L.append(f"| {st} | {ms(s['p50'])} | {ms(s['p90'])} | {ms(s['p95'])} | {ms(s['p99'])} | {ms(s['min'])} | {ms(s['max'])} |")
        if e in net and rs:
            model_only = pct([r["interpret_network"] for r in rs], 50) - net[e]["warm"]["p50"]
            L.append(f"\nEstimated model-side time (interpret p50 − warm network p50): **{ms(model_only)} ms**")
        if rs and any(r["cost_usd"] for r in rs):
            L.append(f"\nMean cost/query ${statistics.mean(r['cost_usd'] for r in rs):.7f} · "
                     f"mean input tokens {statistics.mean(r['input_tokens'] for r in rs):.0f}"
                     + (f" (cached {statistics.mean(r['cached_tokens'] for r in rs):.0f})" if e == "haiku" else ""))
        L.append("")

    if typeahead:
        L += ["## Type-ahead (one lookup per word typed)", "", "| Engine | Lookups | p50 | p95 | max |", "|---|---|---|---|---|"]
        for e, xs in typeahead.items():
            if xs:
                s = stats(xs)
                L.append(f"| {e} | {len(xs)} | {ms(s['p50'])} ms | {ms(s['p95'])} ms | {ms(s['max'])} ms |")
        L.append("")

    if payload:
        L += ["## Payload sensitivity", "", "| Variant | n | Interpret p50 | Interpret p95 | Mean input tokens |", "|---|---|---|---|---|"]
        for name, rs in payload.items():
            rs = [r for r in rs if not r["error"]]
            if rs:
                L.append(f"| {name} | {len(rs)} | {ms(pct([r['interpret_network'] for r in rs], 50))} ms | "
                         f"{ms(pct([r['interpret_network'] for r in rs], 95))} ms | {statistics.mean(r['input_tokens'] for r in rs):.0f} |")
        L.append("")

    if load:
        L += ["## Concurrency load", "", "| Engine | Concurrent | Queries | Throughput | p50 | p95 | p99 | max | Errors (429s) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for e, rows in load.items():
            for d in rows:
                L.append(f"| {e} | {d['concurrency']} | {d['n']} | {d['qps']:.1f} q/s | {ms(d['p50'])} | {ms(d['p95'])} | "
                         f"{ms(d['p99'])} | {ms(d['max'])} | {d['errors']} ({d['rate_limited']}) |")
        L.append("")

    if has_png:
        L += ["## Interpret latency distribution", "", "![histogram](speed_hist.png)", ""]
    (ROOT / "eval" / "speed_report.md").write_text("\n".join(L) + "\n")
    print("wrote eval/speed_report.md, eval/speed_raw.csv" + (", eval/speed_hist.png" if has_png else ""))


if __name__ == "__main__":
    asyncio.run(main())

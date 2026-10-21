# Vehicle-aware smart search for Magento — prototype

Prototype of an AI-assisted search layer for an automotive aftermarket Magento 2 store (Year/Make/Model fitment).
Shoppers type things like `99 ford bronco oil filter`, `f150 2019 floor mats` or `jl wrangler wipers`, and Magento's
native search returns parts that don't fit their vehicle (about 72% of the top-10 in our tests). This prototype
interprets the query, filters by fitment, and ranks by relevance, and compares three engines side by side:

| Engine | What it is |
|---|---|
| Native | Plain BM25 keyword search over the raw query (stand-in for Magento/Elasticsearch search) |
| Claude Haiku | One structured-output call interprets the query and ranks candidates |
| TypeSafe Jev | One call of parallel Choice/Noul questions does the same (faster, cheaper) |

Results on 300 generated test searches: Haiku and Jev both ~81% right-part-for-vehicle (native 18%); Jev ~0.3 s vs Haiku ~1.3 s,
and ~3x cheaper. Full numbers and the reasoning behind the design: [docs/FINDINGS.md](docs/FINDINGS.md).

**This is a prototype, not production code.** If you are implementing it in Magento, start with
**[docs/MAGENTO_INTEGRATION.md](docs/MAGENTO_INTEGRATION.md)**.

## Run it locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # add ANTHROPIC_API_KEY and TYPESAFE_API_KEY (optional; see below)
# put the catalog export + fitment CSV in data/ (see data/README.md), then:
.venv/bin/python scripts/build_index.py    # builds data/catalog.db (~1 s)
.venv/bin/uvicorn app.main:app --port 8000
open "http://localhost:8000/?q=99 ford bronco oil filter"
```
Without keys the AI columns fall back to native search and say so. The client's data is not in this repo.

## Layout
```
app/normalize.py          stage 1 (code): years, makes/models, part-type shortlist, SKUs, typo tolerance
app/interpreters/         stage 2: haiku.py, jev.py (the AI step), rules.py (code-only + "skip AI" test)
app/retrieve.py           stage 3 (code): BM25 + fitment filter + relaxation ladder + explanatory notes
app/suggest.py            type-ahead suggested searches
app/pipeline.py           orchestration, per-stage timings, native fallback on any AI failure
app/main.py               FastAPI: /api/search, /api/suggest, /api/ymm, /api/status
static/                   demo storefront (vanilla HTML/JS) - throwaway, not for Magento
scripts/build_index.py    CSV -> SQLite (FTS5) catalog
eval/                     test-search generator, relevance eval, speed benchmark
tests/test_mocked_apis.py offline check of both AI integrations
```

## Tests and evals
```bash
.venv/bin/python tests/test_mocked_apis.py          # no keys needed
.venv/bin/python eval/make_goldens.py && .venv/bin/python eval/run_eval.py     # accuracy, speed, cost report
.venv/bin/python eval/bench_speed.py --n 200
```

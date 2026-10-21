# Findings, design notes and how the prototype works

This prototype is a mock allmoparparts.com storefront where only search works. Every query runs through three engines side by side:

| Column | What it is |
|---|---|
| **Native keyword search** | BM25 over the raw query with no vehicle awareness. Stands in for Magento's built-in search. |
| **Claude Haiku 5.5** | One structured-output call that interprets the query (year, vehicle, part type, keywords, SKU). |
| **TypeSafe Jev** | One call with parallel Choice/Noul questions that interprets the same query from the same candidates. |

Both model engines feed the **same** retriever: SQLite FTS5 plus a hard fitment filter. Only the "understand the query" step differs, so the comparison is fair.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # add ANTHROPIC_API_KEY and TYPESAFE_API_KEY
.venv/bin/python scripts/build_index.py   # ~1 s → data/catalog.db
.venv/bin/uvicorn app.main:app --port 8000
open "http://localhost:8000/?q=99 ford bronco oil filter"
```

Without keys the app still runs. **Fallback policy:** if a model call fails (no key, timeout, rate limit, server error, refusal), that column falls back to **native keyword search**. Haiku never falls back to Jev, and Jev never falls back to Haiku. The column shows "fell back to native search", and the response carries `"fallback": "native"`.

## How a query flows

```
"99 ford bronco oil filter"
 [1] normalize (code, ~1 ms)   years [1999] · vehicles [Ford Bronco] · part shortlist [Oil Filter, …] · residual "oil filter"
 [2] interpret (model)         Haiku: JSON schema output  |  Jev: Choice(year) Choice(vehicle) Choice(part) Choice(make) Noul(part#)
 [3] retrieve (code, ~5 ms)    BM25 ∩ fitment(Ford Bronco, 1999) ∪ universal → relaxation ladder → notes
 [4] re-rank (optional)        Haiku listwise ordering  |  Jev one Noul per candidate, all in one call
```

Some queries name a vehicle the catalog doesn't cover. For example, Bronco fitment exists only for 2016 and 2021–2027, so for `99 bronco` the retriever relaxes the year and says so: *"We don't have 1999 Ford Bronco fitment data. Catalog covers Bronco: 2021–2027."*

### Why Jev is used this way
Jev doesn't generate text. It picks among options you supply (at most 255 per Choice) and returns calibrated probabilities. TypeSafe's own docs say to let code propose candidates and let Jev choose. So the normalizer proposes candidates:
- years from the query;
- up to 189 vehicles;
- a part-type shortlist of about 40 out of 783.

Jev then picks. When the top options are close (for example Oil Filter 0.45 vs Engine Air Filter 0.35), both are kept. Haiku sees the same candidates and the full vehicle list, which sits in a prompt-cached system prompt.

## How Jev's questions are designed (and why)
Jev picks from options; it doesn't write text. Early versions trailed Haiku on accuracy (78% vs 81%). The cause was the question design, not the model:

| Problem | Fix (following TypeSafe's guidance) |
|---|---|
| Part type asked as one single-pick question, but "floor mats" means *two* part types | One yes/no question per part type (up to 15), all in the same call |
| Year logic left to Jev ("'10 ram 1500" → 1500 DT, a 2019+ truck); Jev reads dates as text | Code drops vehicle generations that don't cover the typed year |
| Jev saw fewer vehicle options than Haiku (typo "ratpor" → no Raptor option) | Offer sibling trims and fuzzy matches of leftover words |
| Literal yes to broad categories ("Plug" for spark plug, "Cover" for seat covers) | Code drops a broad type when a more specific one is chosen |
| Search keywords came from the code's leftovers ("plug", "ratpor …") because Jev can't write them | Code rebuilds keywords *after* Jev picks the vehicle |

Result on 300 searches: Jev **81%** (same as Haiku), understanding **92%** (Haiku 89%), 4.7× faster, 3.4× cheaper. Explicit true/false definitions on the yes/no questions were tried and dropped: they made Jev stricter (understanding 96% → 91%) and cost 40% more.

## What viewers see
The page is styled after the live allmoparparts.com, and everything is in plain terms: **accuracy, speed, cost**.

- **Suggestions while typing.** A dropdown under the search box shows each engine's suggestions side by side, 250 ms after the shopper pauses.
  - Today's site search shows 5 products, like the live site's dropdown.
  - The AI engines also show suggested searches, such as "Oil Filter for 2022 Ford Bronco · 2 parts".
  - The AI columns show **only parts that fit**, each tagged "Fits 2022 Ford Bronco". Today's-search column still shows its non-fitting parts, since that's what shoppers see now.
  - **Relevance ranking happens in the same AI call.** Code first pulls the likely top 10 products, then one call asks the AI both what the shopper means and how relevant each product is. For Jev that's one yes/no question per product, answered in parallel ("speculative fan-out"). For Haiku it's a ranked list in its answer. Only the first page is re-ordered. Use `eval/run_eval.py --no-rank` to measure without it.
- **After a search.** A summary box spells out the comparison: "today's site search showed 8 of 10 parts that don't fit a 1999 Ford Bronco… TypeSafe Jev was 3.3× faster… 3.0× cheaper".
  - Each column has three tiles: **Accuracy** (parts that fit), **Speed** (seconds), **Cost** (per 1,000 searches).
  - Engineers can expand **Technical details** in each column.
- **Cost counts the whole search.** That means every suggestion lookup made while typing plus the final search, because type-ahead multiplies AI calls (about 4 per search).
- **Scoreboard.** Running totals for the session: parts that fit, how fast suggestions and results appear, slowest search, and cost per 1,000 searches.

Toggles:
- **Skip AI for simple searches** answers unambiguous searches with code alone, which is free and instant. About 80% of test searches qualify.
- **AI double-checks result order** adds a second AI call that re-orders the results.
- **Reuse earlier answers** is off by default, so every search pays real time and cost.

Other controls:
- The **Select Fitment** bar sets the vehicle and overrides any vehicle typed in the search.
- Use `?q=...` to open a specific search, or `?type=...` to replay typing for a demo.

### How the live site's dropdown behaves (checked 2026-10-14)
The live site uses Wyomind Elasticsearch autocomplete (`/autocomplete.php`). It fires once typing pauses and shows up to 5 products (thumbnail, name, price), with no suggested searches. It sends a `ymm=` parameter but ignores the vehicle. In our test it **returned nothing for every multi-word query**, including "oil filter", "floor mats", "f150 floor mats" and "99 bronco oil filter". It only worked for single words like "wiper" and for exact part numbers. Responses took 0.27–2.4 s.

## Evaluation

```bash
.venv/bin/python eval/make_goldens.py              # 300 labelled queries generated from the catalog
.venv/bin/python eval/run_eval.py [--fastpath]      # → eval/report.md (plain summary first), eval/results.csv
.venv/bin/python eval/bench_speed.py --n 200       # → eval/speed_report.md, speed_raw.csv, speed_hist.png
.venv/bin/python tests/test_mocked_apis.py         # offline check of both integrations (no keys)
```

**Goldens** sample real (product, fitment) pairs and render the variants shoppers actually type:
- 2-digit and apostrophe years;
- a missing make, or reordered words;
- aliases (`f150`, `jl wrangler`, `ram 1500`);
- injected typos;
- part synonyms (`mud flaps` → Splash Guards).

There are also 25 hand-written edge cases: impossible vehicles, make-only queries, natural language and part numbers. Labels come from the data, never from an engine.

**Relevance metrics** check the top 10 against the fitment table and partnames, so no LLM judge is involved:

| Metric | What it measures |
|---|---|
| Fit P@10 | share of the top 10 that fit the vehicle |
| Part P@10 | share of the top 10 that are the right part type |
| **Fit+Part P@10** | share that are both (the headline metric) |
| Success@3 | at least one fit+part match in the top 3 |
| Zero-result rate | share of queries with no results |

`run_eval.py` also reports:
- parse accuracy per field and per query type;
- latency and cost per engine;
- **Jev calibration**: accuracy bucketed by Jev's reported confidence, which shows whether that confidence is safe for routing.

**Speed** (`bench_speed.py`) covers:
- a network baseline (bare HTTPS round trip to each API host);
- cold vs warm client;
- sequential p50/p90/p95/p99 per stage;
- concurrency at 1/5/10/25 users (throughput and 429s);
- payload sensitivity (Haiku with and without prompt caching; Jev with 5 vs 3 questions);
- a pass/fail verdict against a 300 ms end-to-end / 200 ms interpret p95 budget.

### Results so far (no keys yet)

| Engine | Fit+Part P@10 | Success@3 | Parse accuracy | p95 total |
|---|---|---|---|---|
| Native keyword search | 17.6% | 44.0% | — | 25 ms |
| Rules only (no model) | 79.0% | — | 85.3% | 7 ms |

The rules engine misses typos ("escaladde", "chalenger") and unlisted synonyms ("grill", "trunk mat", "locking lug nuts"). That's the gap the models need to close, and it's worth paying for only if they close it within the latency budget.

**Network finding.** From the dev machine, a warm HTTPS round trip with no model work is about 220 ms to both `api.anthropic.com` and `api.typesafe.ai`. That's already over a 200 ms interpret budget before any inference happens. Deploy the search service in the same region as the APIs before judging either model on speed, and re-run `bench_speed.py` there.

## Data notes (for the client)
- The product export has 35,680 rows, but only 12,769 are unique visible products. The rest are store-view overrides for trp, lfp, mopp and other store views.
- **733 products have fitment only in the `ymm_fitment` attribute**, not in `m2-catalog-fitment.csv`. One example is Mopar Oil Filter 4105409AE (2007–2018 Wrangler JK). The build expands them, adding 6,190 rows. Native search filtered by the CSV would never show these for a vehicle.
- **Some fitment rows pair a model with the wrong make**, such as "Chevrolet Escalade", "Subaru Focus" and "Chevrolet 300". The build lists them and keeps them out of the search vocabulary.
- Near-duplicate part names exist ("Wheel Lock" / "Wheel Locks", "Wheel Flare" / "Fender Flares"). Both are matched.
- Product images load from `allmoparparts.com/media/catalog/product/…`, with a placeholder fallback.

## On the Amazon "RAG" idea
The relevant public dataset is Amazon's **ESCI Shopping Queries** ([amazon-science/esci-data](https://github.com/amazon-science/esci-data)): about 130k real queries with Exact/Substitute/Complement/Irrelevant labels. An automotive slice is available through [shuttie/esci-s](https://github.com/shuttie/esci-s) metadata.

**Don't use it at query time.**
- It contains none of this catalog's SKUs or fitment data.
- An extra retrieval hop adds latency to every search.
- For Haiku it also makes prompts longer, and therefore slower.

The grounding that actually helps is the client's own vocabulary (vehicles and part types), which both engines already get.

ESCI is worth using **offline**: mine automotive queries for phrasing and synonyms, and to enrich `eval/goldens.jsonl`. A better source than ESCI is the client's own Magento `search_query` table: real queries, with hit counts, from their own shoppers. Ask for an export.

## Layout
```
app/                        FastAPI app + pipeline
  normalize.py              stage 1: candidates (years, vehicles, part shortlist, SKUs)
  interpreters/rules.py     code-only resolution + fast-path test
  interpreters/haiku.py     stage 2: Claude Haiku 5.5, JSON-schema output, cached system prompt
  interpreters/jev.py       stage 2: TypeSafe Jev, parallel Choice/Noul
  retrieve.py               stage 3: FTS5 + fitment + relaxation ladder (+ baseline)
  rerank.py                 stage 4 (optional)
  pipeline.py               orchestration + per-stage timings_ms
static/                     mock storefront (vanilla HTML/CSS/JS)
scripts/build_index.py      CSV → data/catalog.db
eval/                       goldens, relevance eval, speed bench
tests/test_mocked_apis.py   offline integration check
```

Pricing assumptions live in `app/interpreters/*.py`:
- **Haiku 5.5:** $0.10 / $0.50 per 1M input/output tokens.
- **Jev:** $0.042 per 1M input tokens. The output price isn't published, so it defaults to the same rate; override it in `.env`.

# Implementing this in Magento 2

Audience: the Magento developer taking this from prototype to the store. Read the README first.

## What to build
A search layer that sits in front of (and falls back to) Magento's existing search:

```
shopper types ──► Magento module (PHP) ──► Search service (this logic, HTTP, <500 ms budget)
                      │  timeout / error / 429 / no key ───► existing native search (always the fallback)
                      ▼
          dropdown (autocomplete) + catalogsearch result page
```

**Recommended: keep the interpreter as a small separate service** (the code in `app/` as a starting point) and have a thin Magento
module call it. Reasons: the normalizer uses RapidFuzz and the AI SDKs are Python; porting to PHP buys little. The module's job
is (1) call the service, (2) render results in the store's templates, (3) fall back to native on any failure.

## The contract the module needs
Prototype endpoints (see `app/main.py`; harden before exposing):

| Endpoint | Use |
|---|---|
| `GET /api/suggest?q=&engine=jev&year=&make=&model=` | Type-ahead: up to 5 fitting products + suggested searches |
| `GET /api/search?q=&engine=jev&...` | Full results page |
| `GET /api/ymm` | Year -> make -> models tree (for reference; Magento already has its own picker) |

Response fields that matter: `results[]` (`sku`, `name`, `price`, `fit`, `fit_label`), `parsed` (year, vehicles, partnames),
`notes[]` (shopper-facing text, e.g. "We don't have 1999 Ford Bronco fitment data…"), `suggestions[]`, `fallback`, `timings_ms`, `usage`.
Return **SKUs in ranked order** and let Magento load/render the products (price, stock, image, URL) so it stays the source of truth.

## Magento-side touch points
- **Autocomplete.** The live store uses Wyomind Elasticsearch autocomplete: the browser calls `/autocomplete.php?store=…&currency=…&cg=…&fallback_url=…&ymm=&q=` and gets HTML
  (up to 5 products). It already sends a `ymm=` parameter but ignores the vehicle, and in testing returned nothing for multi-word queries
  ("oil filter", "floor mats"). Either point that endpoint at the service and keep the HTML shape, or ship a replacement block.
- **Vehicle precedence.** If the shopper has selected a vehicle in the Select Fitment bar, that selection must override any vehicle parsed from the text (the prototype does this via `year/make/model` params).
- **Search results page.** Hook `catalogsearch/result` (plugin on the search collection or a custom result controller) to take the ordered SKU list.
- **Fitment data.** Two sources exist and both are needed: the fitment table (`product_id, sku, year, make, model`) and the `ymm_fitment` product attribute.
  ~730 products have fitment *only* in the attribute; `scripts/build_index.py` expands those ranges. Decide on one source of truth, ideally sync the table from the attribute.
- **Part types.** `partname` attribute (≈780 values) drives part-type matching. Some near-duplicates ("Wheel Lock"/"Wheel Locks") exist; normalize at the source if possible.
- **Store scope.** The prototype indexes every store view's products. The live site appears to be Mopar-only; confirm scope and filter by `product_websites`/store view.
- **Images.** Product images come from the CDN path `…/catalog/product/<base_image>` (prototype uses `https://d3j7mquu4xakgo.cloudfront.net/production/catalog/product/`). In Magento, use the normal image helpers.

## How the interpreter works (so you can port or tune it)
1. **Normalize (code, ~1 ms)** `app/normalize.py`: years (`99`, `'99`, `1999`), makes/models + aliases (`f150`, `jl`, `ram 1500`), typo tolerance (RapidFuzz), part-type shortlist from the ~780 partnames, SKU detection.
2. **Interpret (AI, ~0.3 s Jev / ~1.3 s Haiku)** chooses among the candidates and scores the top-10 products for relevance *in the same call*. Jev only picks from options; code owns year logic and keyword building.
3. **Retrieve (code)** `app/retrieve.py`: BM25 text match ∩ fitment (universal parts always included), then a relaxation ladder (exact year -> any year -> any vehicle) with a shopper-facing note.
4. **Fast path (optional)** `interpreters/rules.is_unambiguous`: ~80% of test searches are clear-cut and need no AI call at all. Recommended in production for speed, cost and resilience.
5. **Fallback.** Any AI failure -> native search. Haiku never falls back to Jev or vice versa.

## Before production (the prototype deliberately skips these)
- **Replace SQLite/FTS5** with the store's Elasticsearch/OpenSearch (or keep a read-only index rebuilt on catalog/fitment changes). Plan an incremental reindex.
- **Secrets & auth.** Keys come from `.env`; use the platform secret store. Put auth/rate limiting on the service; the demo's in-app rate limiter is only a stopgap.
- **Latency budget.** Set a hard timeout (~500 ms for type-ahead) with native fallback. Host the service near the AI providers: from a Mac in our tests ~0.22 s of each call was network distance.
- **Cost control.** Type-ahead fires an AI call per typed word (~5 calls per completed search). Keep the debounce (250 ms), cache identical queries, use the fast path, and cap per-IP/session calls. Measured cost with Jev: about $0.21 per 1,000 searches (about $21/month at 100k searches); Haiku about $0.73.
- **Real queries.** All accuracy numbers come from searches generated from the catalog. Export Magento's `search_query` table and re-run `eval/` on real shopper queries before committing to a vendor.
- **Haiku baseline.** The Haiku column recreates the team's approach; compare against the production Haiku search for a true like-for-like.
- **Data quality.** A few fitment rows pair a model with the wrong make (e.g. Chevrolet Escalade); `build_index.py` prints and excludes the obvious ones. Fix at the source.
- **Observability.** Log query, parse, fallback reason, latency and token usage per request (`timings_ms`, `usage` are already returned).
- **Tests.** `tests/test_mocked_apis.py` covers request/response shape only. Add regression tests from real queries.

## Vendors
- Anthropic Messages API, `claude-haiku-5-5`, structured JSON output, prompt-cached system prompt (`app/interpreters/haiku.py`).
- TypeSafe Jev `POST https://api.typesafe.ai/v1/systemone` via the `typesafe-sdk` package (`app/interpreters/jev.py`). Pricing used: $0.042 per 1M input tokens, output free.

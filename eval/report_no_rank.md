# Search comparison — 100 test searches

## Summary

**Accuracy** — of the parts shown on the first page, how many fit the shopper's vehicle *and* are the part they asked for. **Top result is right** — the very first part shown fits and is what they asked for. **Speed** — typical time until suggestions and results appear. **Cost** — AI cost per 1,000 searches, measured by replaying each search as it's typed (a suggestion lookup per word, about 4.0 per search, plus the final search).

| | Accuracy | Top result is right | Parts shown that don't fit the vehicle | Suggestions appear in | Results appear in | Cost per 1,000 searches | Cost per month* |
|---|---|---|---|---|---|---|---|
| **Claude Haiku** | 81% | 92% | 4% | 1.29 s | 1.33 s | $0.561 | $56.09 |
| **TypeSafe Jev** | 78% | 88% | 6% | 0.27 s | 0.27 s | $0.165 | $16.49 |

\* at 100,000 searches a month.

- TypeSafe Jev is **4.9× faster** and **3.4× cheaper** than Claude Haiku.
- Accuracy: Jev 78% vs Haiku 81%.
- How TypeSafe Jev's cost adds up: 5.0 AI calls per search ≈ 3,925 input tokens → 3.93 million per 1,000 searches × $0.042 per million = **$0.165**. Its ~1,979 output tokens per search are free.
- How Claude Haiku's cost adds up, per 1,000 searches (5.0 AI calls each): 1.31M new input tokens × $0.10 = $0.131 · 21.30M cached input tokens (its reused instructions) × $0.01 = $0.213 · **0.43M output tokens × $0.50 = $0.217** → **$0.561**. Output is 39% of Haiku's cost; Jev charges nothing for output.

---

# Technical appendix

Engines: haiku, jev · fast-path: off · concurrency 8

## Relevance (top 10, graded against fitment table + partnames)

| Engine | Fit P@10 | Part P@10 | **Fit+Part P@10** | Success@3 | Zero results |
|---|---|---|---|---|---|
| haiku | 95.8% | 82.9% | **81.2%** | 94.0% | 0.0% |
| jev | 93.8% | 80.5% | **77.9%** | 92.0% | 0.0% |

## Query understanding (parse accuracy)

| Engine | All fields | Year | Vehicle | Part | Errors |
|---|---|---|---|---|---|
| haiku | 87.0% | 100.0% | 92.0% | 95.0% | 0 |
| jev | 88.0% | 100.0% | 97.0% | 91.0% | 0 |

## Speed & cost (in-process, uncached)

| Engine | Interpret p50 | Interpret p95 | Total p50 | Total p95 | $ / 1k queries | Fast-path rate |
|---|---|---|---|---|---|---|
| haiku | 1322 ms | 2132 ms | 1330 ms | 2143 ms | $0.1234 | 0.0% |
| jev | 267 ms | 403 ms | 273 ms | 410 ms | $0.0368 | 0.0% |

## Parse accuracy by query type

| Type | n | haiku | jev |
|---|---|---|---|
| alias | 11 | 72.7% | 90.9% |
| apostrophe_no_make | 10 | 90.0% | 80.0% |
| canonical | 11 | 90.9% | 90.9% |
| impossible_vehicle | 1 | 100.0% | 0.0% |
| natural_language | 2 | 100.0% | 100.0% |
| no_make | 5 | 60.0% | 80.0% |
| no_year | 11 | 100.0% | 100.0% |
| part_first | 16 | 100.0% | 100.0% |
| reordered | 12 | 83.3% | 83.3% |
| two_digit_year | 10 | 90.0% | 90.0% |
| typo | 11 | 72.7% | 72.7% |

## jev calibration — is reported confidence trustworthy?

| Confidence | n | Parse accuracy |
|---|---|---|
| 0.00–0.50 | 1 | 100.0% |
| 0.50–0.70 | 12 | 66.7% |
| 0.70–0.85 | 7 | 57.1% |
| 0.85–0.95 | 16 | 93.8% |
| 0.95–1.00 | 64 | 93.8% |

## haiku calibration — is reported confidence trustworthy?

| Confidence | n | Parse accuracy |
|---|---|---|
| 0.50–0.70 | 1 | 100.0% |
| 0.70–0.85 | 6 | 83.3% |
| 0.85–0.95 | 49 | 81.6% |
| 0.95–1.00 | 44 | 93.2% |

## Sample parse misses

| Engine | Type | Query | Parsed |
|---|---|---|---|
| haiku | alias | `2014 f 150 rain guards` | y=2014 v=[['Ford', 'F-150']] pn=['Rain Guards'] |
| haiku | no_make | `2025 outback cargo liner` | y=2025 v=[['Subaru', 'Outback']] pn=['Cargo Tub Liner', 'Tailgate Liner'] |
| haiku | alias | `2020 sierra vent visors` | y=2020 v=[['GMC', 'Sierra 1500'], ['GMC', 'Sierra HD']] pn=['Air Deflector'] |
| haiku | reordered | `super duty 2020 rain guards` | y=2020 v=[['Ford', 'Super Duty']] pn=['Rain Guards'] |
| haiku | alias | `2021 sierra 1500 vent visors` | y=2021 v=[['GMC', 'Sierra 1500'], ['Chevrolet', 'Silverado 1500']] pn=['Air Deflector'] |
| haiku | two_digit_year | `24 chevy blazer license plate frame` | y=2024 v=[['Chevrolet', 'Blazer'], ['Chevrolet', 'Blazer EV']] pn=['License Plate Frame'] |
| haiku | apostrophe_no_make | `'26 giulia oil filter` | y=2026 v=[['Alfa Romeo', 'Giulia'], ['Alfa Romeo', 'Giulia Quadrifoglio']] pn=['Oil Filter'] |
| haiku | typo | `silverdo 2020 mud flaps` | y=2020 v=[['Chevrolet', 'Silverado 1500'], ['Chevrolet', 'Silverado 2500']] pn=['Splash Guards'] |
| haiku | reordered | `escalade 2026 trunk mat` | y=2026 v=[['Cadillac', 'Escalade']] pn=['Trunk Mat'] |
| haiku | typo | `2017 f-150 ratpor carpeted floor mats` | y=2017 v=[['Ford', 'F-150'], ['Ford', 'F-150 Raptor']] pn=['Carpeted Floor Mats'] |
| haiku | canonical | `2016 gmc savana rain guards` | y=2016 v=[['GMC', 'Savana']] pn=['Rain Guards'] |
| haiku | typo | `2020 rangger skid plate` | y=2020 v=[['Ford', 'Ranger'], ['Ford', 'Ranger Raptor']] pn=['Skid Plate'] |
| haiku | no_make | `2021 sierra 1500 running boards` | y=2021 v=[['Chevrolet', 'Silverado 1500'], ['GMC', 'Sierra 1500']] pn=['Step Bars'] |
| jev | typo | `2023 soletrra door edge guards` | y=2023 v=[] pn=['Door Edge Guards'] |
| jev | alias | `2014 f 150 rain guards` | y=2014 v=[['Ford', 'F-150']] pn=['Rain Guards'] |
| jev | no_make | `2025 outback cargo liner` | y=2025 v=[['Subaru', 'Outback']] pn=['Cargo Tub Liner'] |
| jev | reordered | `super duty 2020 rain guards` | y=2020 v=[['Ford', 'Super Duty']] pn=['Rain Guards'] |
| jev | impossible_vehicle | `1995 bronco floor mats` | y=1995 v=[['Ford', 'Bronco']] pn=['Mat'] |
| jev | apostrophe_no_make | `'10 ram 1500 wiper blades` | y=2010 v=[['Ram', '1500 DT']] pn=['Wiper Blade'] |
| jev | two_digit_year | `15 fiat 500 floor mats` | y=2015 v=[['Fiat', '500']] pn=['Mat'] |
| jev | reordered | `escalade 2026 trunk mat` | y=2026 v=[['Cadillac', 'Escalade']] pn=['Trunk Mat'] |
| jev | apostrophe_no_make | `'24 forester floor mats` | y=2024 v=[['Subaru', 'Forester']] pn=['Mat'] |
| jev | typo | `2017 f-150 ratpor carpeted floor mats` | y=2017 v=[['Ford', 'F-150']] pn=['Carpeted Floor Mats'] |
| jev | canonical | `2016 gmc savana rain guards` | y=2016 v=[['GMC', 'Savana']] pn=['Rain Guards'] |
| jev | typo | `2017 ipreza floor mats` | y=2017 v=[['Subaru', 'Impreza']] pn=['Mat'] |

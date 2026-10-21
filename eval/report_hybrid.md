# Search comparison — 100 test searches

## Summary

**Accuracy** — of the parts shown on the first page, how many fit the shopper's vehicle *and* are the part they asked for. **Speed** — typical time until suggestions and results appear. **Cost** — AI cost per 1,000 searches, measured by replaying each search as it's typed (a suggestion lookup per word, about 4.0 per search, plus the final search).

| | Accuracy | Parts shown that don't fit the vehicle | Suggestions appear in | Results appear in | Cost per 1,000 searches | Cost per month* |
|---|---|---|---|---|---|---|
| **Claude Haiku** | 81% | 3% | under 0.1 s | under 0.1 s | $0.163 | $16.26 |
| **TypeSafe Jev** | 81% | 3% | under 0.1 s | under 0.1 s | $0.049 | $4.87 |

\* at 100,000 searches a month.

- TypeSafe Jev is **1.3× faster** and **3.3× cheaper** than Claude Haiku.
- Accuracy: Jev 81% vs Haiku 81%.

---

# Technical appendix

Engines: haiku, jev · fast-path: on · concurrency 8

## Relevance (top 10, graded against fitment table + partnames)

| Engine | Fit P@10 | Part P@10 | **Fit+Part P@10** | Success@3 | Zero results |
|---|---|---|---|---|---|
| haiku | 97.1% | 81.6% | **81.0%** | 96.0% | 0.0% |
| jev | 97.1% | 81.2% | **80.6%** | 95.0% | 0.0% |

## Query understanding (parse accuracy)

| Engine | All fields | Year | Vehicle | Part | Errors |
|---|---|---|---|---|---|
| haiku | 90.0% | 100.0% | 96.0% | 92.0% | 0 |
| jev | 90.0% | 100.0% | 97.0% | 91.0% | 0 |

## Speed & cost (in-process, uncached)

| Engine | Interpret p50 | Interpret p95 | Total p50 | Total p95 | $ / 1k queries | Fast-path rate |
|---|---|---|---|---|---|---|
| haiku | 1351 ms | 1932 ms | 7 ms | 1464 ms | $0.0177 | 85.0% |
| jev | 331 ms | 599 ms | 5 ms | 387 ms | $0.0056 | 85.0% |

## Parse accuracy by query type

| Type | n | haiku | jev |
|---|---|---|---|
| alias | 11 | 90.9% | 90.9% |
| apostrophe_no_make | 10 | 90.0% | 90.0% |
| canonical | 11 | 90.9% | 90.9% |
| impossible_vehicle | 1 | 100.0% | 100.0% |
| natural_language | 2 | 100.0% | 100.0% |
| no_make | 5 | 80.0% | 80.0% |
| no_year | 11 | 100.0% | 100.0% |
| part_first | 16 | 87.5% | 87.5% |
| reordered | 12 | 91.7% | 91.7% |
| two_digit_year | 10 | 100.0% | 100.0% |
| typo | 11 | 72.7% | 72.7% |

## jev calibration — is reported confidence trustworthy?

| Confidence | n | Parse accuracy |
|---|---|---|
| 0.00–0.50 | 1 | 100.0% |
| 0.50–0.70 | 2 | 50.0% |
| 0.70–0.85 | 1 | 100.0% |
| 0.85–0.95 | 5 | 80.0% |
| 0.95–1.00 | 6 | 100.0% |

## haiku calibration — is reported confidence trustworthy?

| Confidence | n | Parse accuracy |
|---|---|---|
| 0.70–0.85 | 1 | 100.0% |
| 0.85–0.95 | 12 | 83.3% |
| 0.95–1.00 | 2 | 100.0% |

## Sample parse misses

| Engine | Type | Query | Parsed |
|---|---|---|---|
| haiku | typo | `2023 soletrra door edge guards` | y=2023 v=[['Ford', 'Edge']] pn=['Door', 'Guard'] |
| haiku | part_first | `locking lug nuts for 2012 dodge avenger` | y=2012 v=[['Dodge', 'Avenger']] pn=['Nut'] |
| haiku | no_make | `2025 outback cargo liner` | y=2025 v=[['Subaru', 'Outback']] pn=['Cargo Tub Liner', 'Tailgate Liner'] |
| haiku | alias | `2024 f 150 spark plug` | y=2024 v=[['Chevrolet', 'Spark']] pn=['Plug'] |
| haiku | typo | `silverdo 2020 mud flaps` | y=2020 v=[['Chevrolet', 'Silverado 1500'], ['Chevrolet', 'Silverado 2500']] pn=['Splash Guards'] |
| haiku | canonical | `2011 dodge avenger locking lug nuts` | y=2011 v=[['Dodge', 'Avenger']] pn=['Nut'] |
| haiku | reordered | `escalade 2026 trunk mat` | y=2026 v=[['Cadillac', 'Escalade']] pn=['Trunk Mat'] |
| haiku | apostrophe_no_make | `'11 explorer locking lug nuts` | y=2011 v=[['Ford', 'Explorer']] pn=['Nut'] |
| haiku | part_first | `locking lug nuts for 2018 ram promaster` | y=2018 v=[['Ram', 'Promaster']] pn=['Nut'] |
| haiku | typo | `2017 f-150 ratpor carpeted floor mats` | y=2017 v=[['Ford', 'F-150']] pn=['All Weather Floor Mats', 'Carpeted Floor Mats'] |
| jev | typo | `2023 soletrra door edge guards` | y=2023 v=[['Ford', 'Edge']] pn=['Door', 'Guard'] |
| jev | part_first | `locking lug nuts for 2012 dodge avenger` | y=2012 v=[['Dodge', 'Avenger']] pn=['Nut'] |
| jev | no_make | `2025 outback cargo liner` | y=2025 v=[['Subaru', 'Outback']] pn=['Cargo Tub Liner'] |
| jev | alias | `2024 f 150 spark plug` | y=2024 v=[['Chevrolet', 'Spark']] pn=['Plug'] |
| jev | canonical | `2011 dodge avenger locking lug nuts` | y=2011 v=[['Dodge', 'Avenger']] pn=['Nut'] |
| jev | reordered | `escalade 2026 trunk mat` | y=2026 v=[['Cadillac', 'Escalade']] pn=['Trunk Mat'] |
| jev | apostrophe_no_make | `'11 explorer locking lug nuts` | y=2011 v=[['Ford', 'Explorer']] pn=['Nut'] |
| jev | part_first | `locking lug nuts for 2018 ram promaster` | y=2018 v=[['Ram', 'Promaster']] pn=['Nut'] |
| jev | typo | `2017 f-150 ratpor carpeted floor mats` | y=2017 v=[['Ford', 'F-150']] pn=['All Weather Floor Mats', 'Carpeted Floor Mats'] |
| jev | typo | `2017 ipreza floor mats` | y=2017 v=[['Subaru', 'Impreza']] pn=['Mat'] |

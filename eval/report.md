# Search comparison — 300 test searches

## Summary

**Accuracy** — of the parts shown on the first page, how many fit the shopper's vehicle *and* are the part they asked for. **Top result is right** — the very first part shown fits and is what they asked for. **Speed** — typical time until suggestions and results appear. **Cost** — AI cost per 1,000 searches, measured by replaying each search as it's typed (a suggestion lookup per word, about 3.8 per search, plus the final search).

| | Accuracy | Top result is right | Parts shown that don't fit the vehicle | Suggestions appear in | Results appear in | Cost per 1,000 searches | Cost per month* |
|---|---|---|---|---|---|---|---|
| **Today's site search** | 18% | 30% | 72% | same as results | under 0.1 s | $0 | $0 |
| **Rules only (no AI)** | 79% | 93% | 4% | same as results | under 0.1 s | $0 | $0 |
| **Claude Haiku** | 81% | 95% | 3% | 1.30 s | 1.30 s | $0.727 | $72.69 |
| **TypeSafe Jev** | 81% | 95% | 2% | 0.28 s | 0.28 s | $0.214 | $21.38 |

\* at 100,000 searches a month.

- Today's site search shows parts that don't fit the shopper's vehicle **72%** of the time.
- TypeSafe Jev gets the right part for the right vehicle **81%** of the time, vs 18% today.
- Claude Haiku gets the right part for the right vehicle **81%** of the time, vs 18% today.
- TypeSafe Jev is **4.7× faster** and **3.4× cheaper** than Claude Haiku.
- Accuracy: Jev 81% vs Haiku 81%.
- How TypeSafe Jev's cost adds up: 4.8 AI calls per search ≈ 5,091 input tokens → 5.09 million per 1,000 searches × $0.042 per million = **$0.214**. Its ~2,258 output tokens per search are free.
- How Claude Haiku's cost adds up, per 1,000 searches (4.8 AI calls each): 2.99M new input tokens × $0.10 = $0.299 · 20.29M cached input tokens (its reused instructions) × $0.01 = $0.203 · **0.45M output tokens × $0.50 = $0.225** → **$0.727**. Output is 31% of Haiku's cost; Jev charges nothing for output.
- For comparison, simple rules with no AI at all reach 79% — the AI's value is the remaining gap (typos, nicknames, unusual wording).

---

# Technical appendix

Engines: baseline, rules, haiku, jev · fast-path: off · concurrency 8

## Relevance (top 10, graded against fitment table + partnames)

| Engine | Fit P@10 | Part P@10 | **Fit+Part P@10** | Success@3 | Zero results |
|---|---|---|---|---|---|
| baseline | 28.4% | 74.6% | **17.6%** | 44.0% | 0.0% |
| rules | 96.4% | 80.4% | **79.0%** | 94.7% | 0.0% |
| haiku | 97.2% | 82.4% | **80.9%** | 96.0% | 0.0% |
| jev | 98.0% | 81.9% | **80.8%** | 96.3% | 0.0% |

## Query understanding (parse accuracy)

| Engine | All fields | Year | Vehicle | Part | Errors |
|---|---|---|---|---|---|
| rules | 85.3% | 100.0% | 95.3% | 88.0% | 0 |
| haiku | 89.3% | 100.0% | 93.0% | 96.0% | 0 |
| jev | 92.3% | 100.0% | 98.0% | 94.0% | 0 |

## Speed & cost (in-process, uncached)

| Engine | Interpret p50 | Interpret p95 | Total p50 | Total p95 | $ / 1k queries | Fast-path rate |
|---|---|---|---|---|---|---|
| baseline | — | — | 7 ms | 18 ms | $0.0000 | 0.0% |
| rules | — | — | 5 ms | 10 ms | $0.0000 | 0.0% |
| haiku | 1281 ms | 2045 ms | 1304 ms | 2062 ms | $0.1629 | 0.0% |
| jev | 268 ms | 369 ms | 280 ms | 378 ms | $0.0488 | 0.0% |

## Parse accuracy by query type

| Type | n | rules | haiku | jev |
|---|---|---|---|---|
| alias | 37 | 91.9% | 89.2% | 97.3% |
| apostrophe_no_make | 31 | 80.6% | 90.3% | 90.3% |
| canonical | 31 | 87.1% | 90.3% | 93.5% |
| impossible_vehicle | 3 | 66.7% | 100.0% | 100.0% |
| make_only | 3 | 100.0% | 100.0% | 100.0% |
| natural_language | 3 | 100.0% | 100.0% | 100.0% |
| no_make | 32 | 75.0% | 87.5% | 90.6% |
| no_year | 30 | 100.0% | 100.0% | 96.7% |
| part_first | 30 | 86.7% | 100.0% | 93.3% |
| reordered | 31 | 90.3% | 87.1% | 87.1% |
| sku | 3 | 66.7% | 100.0% | 66.7% |
| two_digit_year | 31 | 90.3% | 80.6% | 96.8% |
| typo | 33 | 66.7% | 78.8% | 84.8% |
| unknown_vehicle | 1 | 100.0% | 100.0% | 100.0% |
| vague | 1 | 100.0% | 0.0% | 100.0% |

## jev calibration — is reported confidence trustworthy?

| Confidence | n | Parse accuracy |
|---|---|---|
| 0.00–0.50 | 6 | 33.3% |
| 0.50–0.70 | 4 | 100.0% |
| 0.70–0.85 | 26 | 80.8% |
| 0.85–0.95 | 64 | 93.8% |
| 0.95–1.00 | 198 | 94.9% |

## haiku calibration — is reported confidence trustworthy?

| Confidence | n | Parse accuracy |
|---|---|---|
| 0.00–0.50 | 1 | 100.0% |
| 0.50–0.70 | 7 | 71.4% |
| 0.70–0.85 | 13 | 53.8% |
| 0.85–0.95 | 193 | 88.1% |
| 0.95–1.00 | 86 | 98.8% |

## Sample parse misses

| Engine | Type | Query | Parsed |
|---|---|---|---|
| rules | apostrophe_no_make | `'24 terrain cargo liner` | y=2024 v=[['GMC', 'Terrain']] pn=[] |
| rules | no_make | `2012 explorer grill` | y=2012 v=[['Ford', 'Explorer']] pn=[] |
| rules | part_first | `grill for 2025 jeep wrangler` | y=2025 v=[['Jeep', 'Wrangler JL']] pn=[] |
| rules | canonical | `2018 ford flex trunk mat` | y=2018 v=[['Ford', 'Flex']] pn=['Trunk Mat'] |
| rules | two_digit_year | `17 ford focus trunk mat` | y=2017 v=[['Ford', 'Focus']] pn=['Trunk Mat'] |
| rules | typo | `2016 forestter spark plug` | y=2016 v=[['Chevrolet', 'Spark']] pn=['Plug'] |
| rules | canonical | `2011 dodge avenger locking lug nuts` | y=2011 v=[['Dodge', 'Avenger']] pn=['Nut'] |
| rules | no_make | `2021 ram 1500 locking lug nuts` | y=2021 v=[['Ram', '1500 DS'], ['Ram', '1500 DT']] pn=['Nut'] |
| rules | typo | `2021 challenegr spark plug` | y=2021 v=[['Chevrolet', 'Spark']] pn=['Plug'] |
| rules | typo | `2020 stelvvio rotors` | y=2020 v=[['Alfa Romeo', 'Stelvio'], ['Alfa Romeo', 'Stelvio Quadrifoglio']] pn=[] |
| rules | canonical | `2023 alfa romeo giulia grill` | y=2023 v=[['Alfa Romeo', 'Giulia']] pn=[] |
| rules | apostrophe_no_make | `'22 brz spark plug` | y=2022 v=[['Chevrolet', 'Spark'], ['Subaru', 'BRZ']] pn=['Plug'] |
| rules | typo | `2009 grand caraan splash guards` | y=2009 v=[['Dodge', 'Grand Caravan'], ['Jeep', 'Grand Cherokee WK']] pn=['Splash Guards'] |
| rules | reordered | `yukon 2026 grill` | y=2026 v=[['GMC', 'Yukon']] pn=[] |
| rules | no_make | `1971 taurus spark plug` | y=1971 v=[['Ford', 'Taurus']] pn=['Plug'] |
| rules | two_digit_year | `24 alfa romeo stelvio quadrifoglio cabin air filter` | y=2024 v=[['Alfa Romeo', 'Stelvio']] pn=['Cabin Air Filter', 'Engine Air Filter'] |
| rules | apostrophe_no_make | `'98 taurus spark plug` | y=1998 v=[['Ford', 'Taurus']] pn=['Plug'] |
| rules | no_make | `2015 f-150 locking lug nuts` | y=2015 v=[['Ford', 'F-150']] pn=['Nut'] |
| rules | typo | `2017 f-150 ratpor carpeted floor mats` | y=2017 v=[['Ford', 'F-150']] pn=['All Weather Floor Mats', 'Carpeted Floor Mats'] |
| rules | part_first | `locking lug nuts for 2012 dodge avenger` | y=2012 v=[['Dodge', 'Avenger']] pn=['Nut'] |
| rules | reordered | `escape 2022 trunk mat` | y=2022 v=[['Ford', 'Escape']] pn=['Trunk Mat'] |
| rules | typo | `2022 musang mah-e air deflector` | y=2022 v=[['Ford', 'Mustang'], ['Ford', 'Mustang Mach-E']] pn=['Air Deflector'] |
| rules | apostrophe_no_make | `'11 town & country locking lug nuts` | y=2011 v=[['Chrysler', 'Town & Country']] pn=['Nut'] |
| rules | alias | `2024 f 150 spark plug` | y=2024 v=[['Chevrolet', 'Spark']] pn=['Plug'] |
| rules | typo | `2024 bronoc rptor air filter` | y=2024 v=[['Ford', 'Bronco'], ['Ford', 'Bronco Raptor']] pn=['Engine Air Filter'] |
| rules | apostrophe_no_make | `'11 explorer locking lug nuts` | y=2011 v=[['Ford', 'Explorer']] pn=['Nut'] |
| rules | no_make | `2023 sierra locking lug nuts` | y=2023 v=[['GMC', 'Sierra 1500']] pn=['Nut'] |
| rules | two_digit_year | `16 subaru forester roof rails` | y=2016 v=[['Subaru', 'Forester']] pn=['Roof Rail'] |
| rules | no_make | `2025 outback cargo liner` | y=2025 v=[['Subaru', 'Outback']] pn=[] |
| rules | apostrophe_no_make | `'22 explorer remote starter` | y=2022 v=[['Ford', 'Explorer']] pn=['Remote'] |
| rules | reordered | `escalade 2026 trunk mat` | y=2026 v=[['Cadillac', 'Escalade']] pn=['Trunk Mat'] |
| rules | alias | `2022 jl spark plug` | y=2022 v=[['Chevrolet', 'Spark']] pn=['Plug'] |
| rules | typo | `2023 soletrra door edge guards` | y=2023 v=[['Ford', 'Edge']] pn=['Door', 'Guard'] |
| rules | typo | `2020 silveardo 2500 tonneau cover` | y=2020 v=[['Ram', '2500']] pn=['Tonneau Cover'] |
| rules | part_first | `locking lug nuts for 2018 ram promaster` | y=2018 v=[['Ram', 'Promaster']] pn=['Nut'] |
| rules | canonical | `2025 gmc hummer ev suv locking lug nuts` | y=2025 v=[['GMC', 'Hummer EV SUV']] pn=['Nut'] |
| rules | no_make | `1981 f-150 raptor spark plug` | y=1981 v=[['Ford', 'F-150 Raptor']] pn=['Plug'] |
| rules | part_first | `locking lug nuts for 2015 dodge grand caravan` | y=2015 v=[['Dodge', 'Grand Caravan']] pn=['Nut'] |
| rules | typo | `2024 f-150 rpator seat covers` | y=2024 v=[['Ford', 'F-150']] pn=['Seat Cover'] |
| rules | impossible_vehicle | `1999 bronco filter` | y=1999 v=[['Ford', 'Bronco']] pn=[] |

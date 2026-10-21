"""Build data/catalog.db from the Magento product export + fitment CSV.

Usage: .venv/bin/python scripts/build_index.py
"""
import csv
import glob
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "catalog.db"
csv.field_size_limit(sys.maxsize)

ATTR_RE = re.compile(r"(?:^|,)([a-z_0-9]+)=")


def parse_additional(raw: str) -> dict:
    """Magento packs attributes as `k=v,k=v`; values may contain commas, so split on `,key=`."""
    out = {}
    matches = list(ATTR_RE.finditer(raw))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        out[m.group(1)] = raw[m.end():end]
    return out


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def leaf_category(cats: str) -> str:
    leaves = [c.split("/")[-1].strip() for c in cats.split(",") if c.strip()]
    leaves = [c for c in leaves if c not in ("Category Pages", "Best Sellers", "Top Selling Parts")]
    return leaves[0] if leaves else ""


def main():
    t0 = time.time()
    found = glob.glob(str(ROOT / "data" / "export_catalog_product_*.csv")) or glob.glob(str(ROOT / "export_catalog_product_*.csv"))
    if not found:
        raise SystemExit("Put export_catalog_product_*.csv and m2-catalog-fitment.csv in data/ (see data/README.md)")
    product_csv = sorted(found)[-1]
    fitment_csv = ROOT / "data" / "m2-catalog-fitment.csv"
    if not fitment_csv.exists():
        fitment_csv = ROOT / "m2-catalog-fitment.csv"
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(
        """
        CREATE TABLE products(sku TEXT PRIMARY KEY, name TEXT, partname TEXT, brand TEXT,
            category TEXT, price REAL, image TEXT, url_key TEXT, is_universal INTEGER,
            ymm_fitment TEXT, websites TEXT, description TEXT);
        CREATE TABLE fitment(sku TEXT, year INTEGER, make TEXT, model TEXT);
        CREATE VIRTUAL TABLE products_fts USING fts5(sku UNINDEXED, name, partname, brand, category, description,
            tokenize='porter unicode61');
        CREATE TABLE vocab_models(make TEXT, model TEXT, min_year INTEGER, max_year INTEGER, years TEXT, n INTEGER);
        CREATE TABLE vocab_partnames(partname TEXT PRIMARY KEY, n INTEGER);
        """
    )

    n = 0
    seen = set()
    partnames = defaultdict(int)
    with open(product_csv, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r["store_view_code"] or r["sku"] in seen:
                continue
            if r["visibility"] == "Not Visible Individually" or r["product_online"] == "2":
                continue
            seen.add(r["sku"])
            a = parse_additional(r["additional_attributes"])
            partname = a.get("partname", "").strip()
            try:
                ymm = ", ".join(json.loads(a.get("ymm_fitment", "[]") or "[]"))
            except json.JSONDecodeError:
                ymm = ""
            desc = strip_html(r["description"])[:1500]
            row = (
                r["sku"], r["name"].strip(), partname, a.get("brand", ""), leaf_category(r["categories"]),
                float(r["price"] or 0), r["base_image"], r["url_key"],
                1 if a.get("is_universal") == "Yes" else 0, ymm, r["product_websites"], desc,
            )
            con.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            con.execute(
                "INSERT INTO products_fts VALUES (?,?,?,?,?,?)",
                (r["sku"], row[1], partname, row[3], row[4], desc),
            )
            if partname:
                partnames[partname] += 1
            n += 1

    years_by_model = defaultdict(set)
    count_by_model = defaultdict(int)
    fit_rows = []
    with open(fitment_csv) as f:
        for r in csv.DictReader(f):
            if not r["year"].isdigit():
                continue
            y = int(r["year"])
            fit_rows.append((r["sku"], y, r["make"], r["model"]))
            years_by_model[(r["make"], r["model"])].add(y)
            count_by_model[(r["make"], r["model"])] += 1
    # ~730 products carry fitment only in the `ymm_fitment` attribute ("2007-2018 Jeep Wrangler JK"),
    # not in the fitment CSV. Expand those ranges so search doesn't miss them.
    makes = {mk for mk, _ in years_by_model} | {"Alfa Romeo", "Mercury", "Plymouth", "Eagle", "Hummer", "Pontiac", "Buick", "Saturn"}
    skus_with_fit = {s for s, *_ in fit_rows}
    range_re = re.compile(r"^(\d{4})(?:-(\d{4}))?\s+(.+)$")
    added = 0
    for sku, ymm in con.execute("SELECT sku, ymm_fitment FROM products WHERE ymm_fitment <> ''").fetchall():
        if sku in skus_with_fit:
            continue
        for part in ymm.split(", "):
            m = range_re.match(part.strip())
            if not m:
                continue
            y1, y2, rest = int(m.group(1)), int(m.group(2) or m.group(1)), m.group(3)
            make = next((mk for mk in sorted(makes, key=len, reverse=True) if rest.startswith(mk + " ")), None)
            if not make:
                continue
            model = rest[len(make) + 1:].strip()
            for y in range(y1, y2 + 1):
                fit_rows.append((sku, y, make, model))
                years_by_model[(make, model)].add(y)
                count_by_model[(make, model)] += 1
                added += 1
    print(f"expanded {added} fitment rows from ymm_fitment attributes")
    con.executemany("INSERT INTO fitment VALUES (?,?,?,?)", fit_rows)
    con.execute("CREATE INDEX ix_fit_mmy ON fitment(make, model, year)")
    con.execute("CREATE INDEX ix_fit_sku ON fitment(sku)")
    # Data-quality: a handful of rows pair a model with the wrong make ("Chevrolet Escalade", "Subaru Focus").
    # Keep them in `fitment`, but leave them out of the search vocabulary so they don't hijack queries.
    by_model_name = defaultdict(list)
    for (mk, md), rows_n in count_by_model.items():
        by_model_name[md].append((rows_n, mk))
    suspect = {
        (mk, md) for (mk, md), rows_n in count_by_model.items()
        if rows_n <= 3 and any(other != mk and on >= 10 * rows_n for on, other in by_model_name[md])
    }
    for mk, md in sorted(suspect):
        print(f"  data-quality: '{mk} {md}' has {count_by_model[(mk, md)]} fitment row(s) - likely wrong make; excluded from vocab")
    con.executemany(
        "INSERT INTO vocab_models VALUES (?,?,?,?,?,?)",
        [
            (mk, md, min(ys), max(ys), ",".join(map(str, sorted(ys))), count_by_model[(mk, md)])
            for (mk, md), ys in sorted(years_by_model.items()) if (mk, md) not in suspect
        ],
    )
    con.executemany("INSERT INTO vocab_partnames VALUES (?,?)", sorted(partnames.items()))
    con.commit()
    print(
        f"products={n} fitment_rows={len(fit_rows)} models={len(years_by_model)} "
        f"partnames={len(partnames)} in {time.time() - t0:.1f}s -> {DB}"
    )


if __name__ == "__main__":
    main()

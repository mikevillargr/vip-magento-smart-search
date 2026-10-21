"""Stage 3 (shared): turn a ParsedQuery into ranked products.

FTS5/BM25 on text, hard fitment filter when a vehicle is known, partname boost, and a
relaxation ladder (exact YMM -> any year -> any vehicle) that explains itself to the shopper.
"""
from __future__ import annotations

import re

from .catalog import Catalog
from .types import ParsedQuery

FTS_WEIGHTS = "0, 10.0, 8.0, 1.0, 2.0, 0.5"  # sku, name, partname, brand, category, description
FTS_LIMIT = 3000


def _fts_query(text: str, mode: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1]
    if not toks:
        return ""
    joiner = " " if mode == "and" else " OR "
    return joiner.join(f'"{t}"*' for t in toks)


def fts(cat: Catalog, text: str, mode: str = "and") -> dict[str, float]:
    q = _fts_query(text, mode)
    if not q:
        return {}
    rows = cat.con.execute(
        f"SELECT sku, bm25(products_fts, {FTS_WEIGHTS}) AS s FROM products_fts WHERE products_fts MATCH ? "
        f"ORDER BY s LIMIT {FTS_LIMIT}",
        (q,),
    ).fetchall()
    return {r["sku"]: -r["s"] for r in rows}  # higher is better


def partname_skus(cat: Catalog, partnames: list[str]) -> set[str]:
    if not partnames:
        return set()
    marks = ",".join("?" * len(partnames))
    return {r[0] for r in cat.con.execute(f"SELECT sku FROM products WHERE partname IN ({marks})", partnames)}


def _vehicle_text(cat: Catalog, p: ParsedQuery) -> str:
    if not p.vehicles:
        return ""
    if len(p.vehicles) == 1:
        mk, md = p.vehicles[0]
        return f"{mk} {md}"
    makes = {v[0] for v in p.vehicles}
    if len(makes) == 1 and len(p.vehicles) == sum(1 for v in cat.models if v[0] in makes):
        return next(iter(makes))
    return " / ".join(f"{mk} {md}" for mk, md in p.vehicles[:3]) + ("…" if len(p.vehicles) > 3 else "")


def retrieve(cat: Catalog, p: ParsedQuery, limit: int = 24) -> dict:
    notes: list[str] = []
    year = p.year
    vehicles = list(p.vehicles)
    vtext = _vehicle_text(cat, p)

    if vehicles and year is not None and not any(year in cat.models.get(v, []) for v in vehicles):
        spans = "; ".join(f"{md}: {cat.year_span(mk, md)}" for mk, md in vehicles[:3])
        notes.append(f"We don't have {year} {vtext} fitment data. Catalog covers {spans}. Showing parts for all years.")
        year = None

    # Text side.
    text_scores = fts(cat, p.keywords, "and") if p.keywords else {}
    if p.keywords and not text_scores:
        text_scores = fts(cat, p.keywords, "or")
    pn = partname_skus(cat, p.partnames)
    candidates = set(text_scores) | pn
    vague = not candidates  # e.g. "bronco accessories": no part signal at all

    def apply_fitment(vs, y):
        if not vs:
            return candidates if not vague else set()
        fit = cat.fitting_skus(vs, y)
        pool = fit if vague else (candidates & (fit | cat.universal_skus()))
        return pool

    pool = apply_fitment(vehicles, year)
    stage = "exact" if vehicles else "keywords"
    if not pool and vehicles and year is not None:
        pool = apply_fitment(vehicles, None)
        if pool:
            notes.append(f"Nothing matched for {year} {vtext}; showing other model years.")
            year = None
            stage = "any_year"
    if not pool and vehicles and not vague:
        pool = set(candidates)
        notes.append(f"No matching parts listed for {vtext}; showing results for all vehicles.")
        stage = "any_vehicle"
    if vague and vehicles and pool:
        notes.append(f"Showing all parts that fit {(str(year) + ' ') if year else ''}{vtext}.")
    if pool & pn:
        # A part type was identified: drop weak keyword-only matches (e.g. "oil" + "filter" buried in a description).
        best = max((text_scores.get(s, 0.0) for s in pool), default=0.0)
        pool = {s for s in pool if s in pn or text_scores.get(s, 0.0) >= 0.5 * best}

    # SKU lookups go first regardless.
    sku_first = [cat.canonical_sku(p.sku)] if p.sku and cat.has_sku(p.sku) else []

    # Status per result relative to the vehicle the shopper asked for.
    # If the catalog has no data for the requested year (e.g. 1999 Bronco), judge fit on the vehicle alone;
    # the note above already tells the shopper about the year gap.
    exact_fit = cat.fitting_skus(vehicles, _known_year(cat, vehicles, p.year)) if vehicles else set()
    any_year_fit = cat.fitting_skus(vehicles, None) if vehicles and p.year is not None else exact_fit
    universal = cat.universal_skus()
    pnset = set(p.partnames)

    ranked = sorted(
        pool - set(sku_first),
        key=lambda s: (s in pn, s in exact_fit, text_scores.get(s, 0.0)),
        reverse=True,
    )
    ordered = sku_first + ranked
    total = len(ordered)
    rows = _load(cat, ordered[:limit])
    results = []
    for s in ordered[:limit]:
        r = rows.get(s)
        if not r:
            continue
        if not vehicles:
            status = "universal" if s in universal else "n/a"
        elif s in exact_fit:
            status = "fits"
        elif s in universal:
            status = "universal"
        elif s in any_year_fit:
            status = "other_years"
        else:
            status = "unverified"
        results.append({
            "sku": s, "name": r["name"], "partname": r["partname"], "brand": r["brand"],
            "price": round(r["price"], 2), "image": r["image"], "url_key": r["url_key"],
            "ymm_fitment": r["ymm_fitment"], "fit": status, "partname_match": r["partname"] in pnset,
        })
    return {"results": results, "total": total, "notes": notes, "stage": stage}


def _load(cat: Catalog, skus: list[str]) -> dict:
    if not skus:
        return {}
    marks = ",".join("?" * len(skus))
    return {
        r["sku"]: r
        for r in cat.con.execute(
            f"SELECT sku, name, partname, brand, price, image, url_key, ymm_fitment FROM products WHERE sku IN ({marks})",
            skus,
        )
    }


def baseline(cat: Catalog, query: str, limit: int = 24) -> dict:
    """Stand-in for native Magento search: the raw query as OR'd keywords, BM25, no vehicle awareness."""
    scores = fts(cat, query, "or")
    ordered = sorted(scores, key=scores.get, reverse=True)
    exact_sku = [cat.canonical_sku(t) for t in query.split() if cat.has_sku(t)]
    ordered = exact_sku + [s for s in ordered if s not in exact_sku]
    rows = _load(cat, ordered[:limit])
    results = [
        {
            "sku": s, "name": rows[s]["name"], "partname": rows[s]["partname"], "brand": rows[s]["brand"],
            "price": round(rows[s]["price"], 2), "image": rows[s]["image"], "url_key": rows[s]["url_key"],
            "ymm_fitment": rows[s]["ymm_fitment"], "fit": "n/a", "partname_match": False,
        }
        for s in ordered[:limit] if s in rows
    ]
    return {"results": results, "total": len(ordered), "notes": [], "stage": "keywords"}


def _known_year(cat: Catalog, vehicles: list[tuple[str, str]], year: int | None) -> int | None:
    return year if year is not None and any(year in cat.models.get(v, []) for v in vehicles) else None


def annotate_fit(cat: Catalog, results: list[dict], vehicles: list[tuple[str, str]], year: int | None) -> None:
    """Label results against a reference vehicle (used to show how the baseline does on fitment)."""
    if not vehicles:
        return
    exact = cat.fitting_skus(vehicles, _known_year(cat, vehicles, year))
    any_year = cat.fitting_skus(vehicles, None)
    universal = cat.universal_skus()
    for r in results:
        s = r["sku"]
        r["fit"] = ("fits" if s in exact else "universal" if s in universal
                    else "other_years" if s in any_year else "unverified")

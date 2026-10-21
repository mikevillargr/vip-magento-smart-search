"""Type-ahead suggestions built from an interpreted (possibly partial) query.

Native mirrors the live site's Wyomind dropdown: top 5 products, no vehicle awareness.
AI engines add suggested searches ("Oil Filter for 2022 Ford Bronco · 2 parts") built from their parse.
"""
from __future__ import annotations

from .catalog import Catalog
from .retrieve import partname_skus
from .types import ParsedQuery


def _vehicle_label(p: ParsedQuery) -> str:
    if not p.vehicles:
        return ""
    makes = {mk for mk, _ in p.vehicles}
    if len(p.vehicles) > 3 and len(makes) == 1:
        v = next(iter(makes))
    else:
        v = " / ".join(f"{mk} {md}" for mk, md in p.vehicles[:2])
    return f"{p.year} {v}" if p.year else v


def suggested_searches(cat: Catalog, p: ParsedQuery, limit: int = 4) -> list[dict]:
    vlabel = _vehicle_label(p)
    year_ok = p.year is not None and p.vehicles and any(p.year in cat.models.get(v, []) for v in p.vehicles)
    fit = cat.fitting_skus(p.vehicles, p.year if year_ok else None) | cat.universal_skus() if p.vehicles else None
    out = []
    if p.partnames:
        for pn in p.partnames[:limit]:
            skus = partname_skus(cat, [pn])
            n = len(skus & fit) if fit is not None else len(skus)
            if n:
                label = f"{pn} for {vlabel}" if vlabel else pn
                out.append({"label": label, "query": label.lower(), "count": n})
    elif p.vehicles and fit:
        # Vehicle but no part yet ("2022 bronco"): most common part types that fit it.
        marks = ",".join("?" * len(fit))
        rows = cat.con.execute(
            f"SELECT partname, COUNT(*) n FROM products WHERE sku IN ({marks}) AND partname <> '' "
            f"GROUP BY partname ORDER BY n DESC LIMIT ?", [*fit, limit]
        ).fetchall()
        for r in rows:
            label = f"{r['partname']} for {vlabel}"
            out.append({"label": label, "query": label.lower(), "count": r["n"]})
    return out

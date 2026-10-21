"""Code-only interpretation of the normalizer's candidates. Doubles as the fast-path:
when `is_unambiguous` is True the model call is skipped."""
from __future__ import annotations

from ..catalog import Catalog
from ..normalize import Candidates
from ..types import ParsedQuery


def resolve(c: Candidates, cat: Catalog, norm=None) -> ParsedQuery:
    year = c.years[0] if c.years else None
    vehicles = list(c.vehicles)
    if c.vehicle_exact:
        # "bronco" -> Ford Bronco, not Bronco Sport/Raptor.
        vehicles = [v for v in vehicles if v in c.vehicle_exact] or vehicles
    if year and len(vehicles) > 1:
        # "2015 wrangler" -> Wrangler JK. Keep the original set if nothing covers the year (impossible vehicle).
        vehicles = [v for v in vehicles if year in cat.models.get(v, [])] or vehicles
    if not vehicles and c.makes:
        vehicles = [v for v in cat.models if v[0] in c.makes]
    return ParsedQuery(
        year=year,
        vehicles=vehicles,
        partnames=c.part_hits,
        keywords=norm.keywords_for(c, vehicles) if norm and vehicles else c.residual,
        sku=c.skus[0] if c.skus else None,
    )


def is_unambiguous(c: Candidates, p: ParsedQuery) -> bool:
    """Safe to skip the model: at most one year, one concrete vehicle (or none / make-only),
    no typo correction, and the part side is either a known part type, a SKU, or empty."""
    make_only = bool(c.makes) and not c.vehicles
    return (
        len(c.years) <= 1
        and (len(p.vehicles) <= 1 or make_only)
        and not c.fuzzy_used
        and (bool(c.part_hits) or bool(c.skus) or not c.residual)
    )

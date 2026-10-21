"""Read-only access to data/catalog.db, with the vocab held in memory."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.db"


class Catalog:
    def __init__(self, path: Path = DB_PATH):
        if not path.exists():
            raise SystemExit(f"{path} missing - run: .venv/bin/python scripts/build_index.py")
        self.path = path
        self._local = threading.local()
        con = self.con
        # (make, model) -> sorted list of years with fitment
        self.models: dict[tuple[str, str], list[int]] = {
            (r["make"], r["model"]): [int(y) for y in r["years"].split(",")]
            for r in con.execute("SELECT make, model, years FROM vocab_models ORDER BY n DESC")
        }
        self.partnames: dict[str, int] = {
            r["partname"]: r["n"] for r in con.execute("SELECT partname, n FROM vocab_partnames ORDER BY n DESC")
        }
        self.skus: dict[str, str] = {r[0].upper(): r[0] for r in con.execute("SELECT sku FROM products")}

    @property
    def con(self) -> sqlite3.Connection:
        c = getattr(self._local, "con", None)
        if c is None:
            c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            c.row_factory = sqlite3.Row
            self._local.con = c
        return c

    def has_sku(self, sku: str) -> bool:
        return sku.upper() in self.skus

    def canonical_sku(self, sku: str) -> str | None:
        """Shoppers type part numbers in any case; the catalog has mixed-case SKUs."""
        return self.skus.get(sku.upper())

    def vehicle_label(self, make: str, model: str) -> str:
        ys = self.models.get((make, model), [])
        span = f"{ys[0]}–{ys[-1]}" if ys else "no fitment"
        return f"{make} {model} ({span})"

    def year_span(self, make: str, model: str) -> str:
        ys = self.models.get((make, model), [])
        if not ys:
            return "no years"
        # Compress gaps: [2016, 2021..2027] -> "2016, 2021–2027"
        runs, start, prev = [], ys[0], ys[0]
        for y in ys[1:] + [None]:
            if y is not None and y == prev + 1:
                prev = y
                continue
            runs.append(f"{start}" if start == prev else f"{start}–{prev}")
            if y is not None:
                start = prev = y
        return ", ".join(runs)

    def fitting_skus(self, vehicles: list[tuple[str, str]], year: int | None) -> set[str]:
        if not vehicles:
            return set()
        clauses = " OR ".join(["(make=? AND model=?)"] * len(vehicles))
        params: list = [x for v in vehicles for x in v]
        sql = f"SELECT DISTINCT sku FROM fitment WHERE ({clauses})"
        if year is not None:
            sql += " AND year=?"
            params.append(year)
        return {r[0] for r in self.con.execute(sql, params)}

    def universal_skus(self) -> set[str]:
        if not hasattr(self, "_universal"):
            self._universal = {r[0] for r in self.con.execute("SELECT sku FROM products WHERE is_universal=1")}
        return self._universal

    def fits(self, sku: str, year: int | None, make: str, model: str) -> bool:
        sql = "SELECT 1 FROM fitment WHERE sku=? AND make=? AND model=?"
        params: list = [sku, make, model]
        if year is not None:
            sql += " AND year=?"
            params.append(year)
        return self.con.execute(sql + " LIMIT 1", params).fetchone() is not None

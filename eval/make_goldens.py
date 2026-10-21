"""Generate labelled search queries from the catalog itself.

Samples (product, fitment row) pairs and renders shopper-style variants (2-digit years, dropped make,
aliases, typos, reordering, synonyms). Labels come from the data, not from any engine, so they can
grade every engine. Hand-written edge cases are appended.

Usage: .venv/bin/python eval/make_goldens.py [--n 300] [--seed 7]
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "eval" / "goldens.jsonl"

# partname -> how shoppers say it (first entry is the plain form)
PART_PHRASES = {
    "Splash Guards": ["splash guards", "mud flaps", "mud guards"],
    "All Weather Floor Mats": ["all weather floor mats", "floor mats", "rubber floor mats", "floor liners"],
    "Carpeted Floor Mats": ["carpeted floor mats", "carpet floor mats", "floor mats"],
    "Seat Cover": ["seat covers", "seat cover"],
    "Air Deflector": ["air deflector", "bug deflector", "vent visors", "rain guards"],
    "Cargo Mat": ["cargo mat", "trunk mat", "cargo liner"],
    "Exhaust System": ["exhaust system", "exhaust", "cat back exhaust"],
    "Spoiler": ["spoiler", "rear spoiler"],
    "Wiper Blade": ["wiper blades", "wipers", "wiper blade"],
    "Tonneau Cover": ["tonneau cover", "bed cover", "tonneau"],
    "Grille": ["grille", "grill"],
    "Door Edge Guards": ["door edge guards"],
    "Sill Guard": ["sill guards", "door sill guards"],
    "Step Bars": ["step bars", "running boards", "side steps"],
    "Engine Air Filter": ["engine air filter", "air filter"],
    "Car Cover": ["car cover"],
    "Roof Rack": ["roof rack", "roof rails"],
    "Remote Start": ["remote start", "remote starter"],
    "Hitch Receiver": ["hitch receiver", "trailer hitch", "hitch"],
    "Wheel Flare": ["wheel flares", "fender flares"],
    "Fender Flares": ["fender flares"],
    "Fog Light": ["fog lights", "fog light"],
    "Oil Filter": ["oil filter"],
    "Cabin Air Filter": ["cabin air filter", "cabin filter"],
    "Spark Plug": ["spark plugs", "spark plug"],
    "Brake Pad": ["brake pads", "brake pad"],
    "Cold Air Intake": ["cold air intake", "intake"],
    "Bed Mat": ["bed mat", "truck bed mat"],
    "Tow Hook": ["tow hooks", "tow hook"],
    "Wheel Lock": ["wheel locks", "locking lug nuts"],
    "License Plate Frame": ["license plate frame"],
    "Sun Shade": ["sun shade", "windshield sunshade"],
    "Brake Rotor": ["brake rotors", "rotors"],
    "Skid Plate": ["skid plate"],
    "Soft Top": ["soft top"],
    "Bike Rack": ["bike rack"],
}

# (make, model) -> shopper aliases
MODEL_ALIASES = {
    ("Ford", "F-150"): ["f150", "f-150", "f 150"],
    ("Ford", "Super Duty"): ["f250", "f350", "super duty"],
    ("Jeep", "Wrangler JL"): ["jl wrangler", "wrangler jl", "jl"],
    ("Jeep", "Wrangler JK"): ["jk wrangler", "wrangler jk", "jk"],
    ("Ram", "1500 DS"): ["ram 1500"],
    ("Ram", "1500 DT"): ["ram 1500"],
    ("Chevrolet", "Silverado 1500"): ["silverado", "silverado 1500"],
    ("GMC", "Sierra 1500"): ["sierra", "sierra 1500"],
    ("Ford", "Mustang"): ["mustang", "stang"],
    ("Ford", "Mustang Mach-E"): ["mach e", "mach-e"],
    ("Jeep", "Grand Cherokee WK"): ["grand cherokee"],
}
MAKE_ALIASES = {"Chevrolet": ["chevy", "chevrolet"]}

TEMPLATES = [
    ("canonical", "{year} {make} {model} {part}"),
    ("two_digit_year", "{yy} {make} {model} {part}"),
    ("apostrophe_no_make", "'{yy} {model} {part}"),
    ("no_make", "{year} {model} {part}"),
    ("reordered", "{model} {year} {part}"),
    ("part_first", "{part} for {year} {make} {model}"),
    ("alias", "{year} {alias} {part}"),
    ("typo", "{year} {typo_model} {part}"),
    ("no_year", "{make} {model} {part}"),
]

EDGE_CASES = [
    # impossible vehicles (Bronco fitment only 2021+): engine should parse, then the store explains
    {"query": "99 ford bronco oil filter", "year": 1999, "vehicles": [["Ford", "Bronco"]], "parts": ["Oil Filter"], "type": "impossible_vehicle"},
    {"query": "1999 bronco filter", "year": 1999, "vehicles": [["Ford", "Bronco"]], "parts": ["Oil Filter", "Engine Air Filter", "Cabin Air Filter"], "type": "impossible_vehicle"},
    {"query": "1995 bronco floor mats", "year": 1995, "vehicles": [["Ford", "Bronco"]], "parts": ["All Weather Floor Mats", "Carpeted Floor Mats"], "type": "impossible_vehicle"},
    # vague / make-only
    {"query": "bronco accessories", "year": None, "vehicles": [["Ford", "Bronco"]], "parts": [], "type": "vague"},
    {"query": "chevy floor mats", "year": None, "vehicles": "make:Chevrolet", "parts": ["All Weather Floor Mats", "Carpeted Floor Mats"], "type": "make_only"},
    {"query": "jeep wipers", "year": None, "vehicles": "make:Jeep", "parts": ["Wiper Blade"], "type": "make_only"},
    {"query": "subaru oil filter", "year": None, "vehicles": "make:Subaru", "parts": ["Oil Filter"], "type": "make_only"},
    # hard aliases
    {"query": "2016 f250 running boards", "year": 2016, "vehicles": [["Ford", "Super Duty"]], "parts": ["Step Bars"], "type": "alias"},
    {"query": "jlu mud flaps", "year": None, "vehicles": [["Jeep", "Wrangler JL"]], "parts": ["Splash Guards"], "type": "alias"},
    {"query": "2014 wrangler unlimited bug deflector", "year": 2014, "vehicles": [["Jeep", "Wrangler JK"]], "parts": ["Air Deflector"], "type": "alias"},
    {"query": "2021 raptor grill", "year": 2021, "vehicles": [["Ford", "F-150 Raptor"], ["Ford", "Bronco Raptor"]], "parts": ["Grille"], "type": "alias"},
    {"query": "hellcat exhaust", "year": None, "vehicles": [["Dodge", "Challenger"], ["Dodge", "Charger"]], "parts": ["Exhaust System"], "type": "alias"},
    {"query": "wrx sti 2019 spoiler", "year": 2019, "vehicles": [["Subaru", "STI"], ["Subaru", "WRX"]], "parts": ["Spoiler"], "type": "alias"},
    {"query": "mach e floor liners", "year": None, "vehicles": [["Ford", "Mustang Mach-E"]], "parts": ["All Weather Floor Mats"], "type": "alias"},
    # typos
    {"query": "silverdo 2020 mud flaps", "year": 2020, "vehicles": [["Chevrolet", "Silverado 1500"]], "parts": ["Splash Guards"], "type": "typo"},
    {"query": "2019 tacoma floor mats", "year": 2019, "vehicles": [], "parts": ["All Weather Floor Mats", "Carpeted Floor Mats"], "type": "unknown_vehicle"},
    {"query": "2020 chalenger spoiler", "year": 2020, "vehicles": [["Dodge", "Challenger"]], "parts": ["Spoiler"], "type": "typo"},
    {"query": "2018 mustang exaust", "year": 2018, "vehicles": [["Ford", "Mustang"]], "parts": ["Exhaust System"], "type": "typo"},
    {"query": "outback 2017 cargo liner", "year": 2017, "vehicles": [["Subaru", "Outback"]], "parts": ["Cargo Mat"], "type": "no_make"},
    # natural language
    {"query": "need new wipers for my 2015 grand cherokee", "year": 2015, "vehicles": [["Jeep", "Grand Cherokee WK"]], "parts": ["Wiper Blade"], "type": "natural_language"},
    {"query": "what floor mats fit a 2020 ram 1500", "year": 2020, "vehicles": [["Ram", "1500 DS"], ["Ram", "1500 DT"]], "parts": ["All Weather Floor Mats", "Carpeted Floor Mats"], "type": "natural_language"},
    {"query": "looking for a hitch for 2019 explorer", "year": 2019, "vehicles": [["Ford", "Explorer"]], "parts": ["Hitch Receiver"], "type": "natural_language"},
    # part numbers
    {"query": "4105409AE", "year": None, "vehicles": [], "parts": [], "sku": "4105409AE", "type": "sku"},
    {"query": "4781452BB", "year": None, "vehicles": [], "parts": [], "sku": "4781452bb", "type": "sku"},
    {"query": "MB3Z-9601-A air filter", "year": None, "vehicles": [], "parts": ["Engine Air Filter"], "sku": "MB3Z-9601-A", "type": "sku"},
]


def typo(word: str, rng: random.Random) -> str:
    if len(word) < 6:
        return word
    i = rng.randrange(1, len(word) - 1)
    op = rng.choice(["drop", "swap", "double"])
    if op == "drop":
        return word[:i] + word[i + 1:]
    if op == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return word[:i] + word[i] + word[i:]


def acceptable_vehicles(con, make: str, model: str, year: int | None, dropped_code: bool) -> list[list[str]]:
    """When the shopper drops a chassis code ('wrangler' for 'Wrangler JL'), any generation covering the year is right."""
    if not dropped_code:
        return [[make, model]]
    base = model.split()[0]
    rows = con.execute(
        "SELECT DISTINCT model FROM fitment WHERE make=? AND (model=? OR model LIKE ?)" + (" AND year=?" if year else ""),
        [make, base, base + " %"] + ([year] if year else []),
    ).fetchall()
    return [[make, r[0]] for r in rows] or [[make, model]]


def shopper_model(model: str) -> tuple[str, bool]:
    """'Wrangler JL' -> ('wrangler', True); 'F-150' -> ('f-150', False)."""
    words = model.split()
    if len(words) == 2 and words[1].isupper() and len(words[1]) <= 3 and not words[0].isdigit():
        return words[0].lower(), True
    if model in ("1500 DS", "1500 DT"):
        return "1500", True
    return model.lower(), False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    con = sqlite3.connect(ROOT / "data" / "catalog.db")

    marks = ",".join("?" * len(PART_PHRASES))
    pairs = con.execute(
        f"SELECT p.sku, p.partname, f.year, f.make, f.model FROM products p JOIN fitment f ON f.sku=p.sku "
        f"WHERE p.partname IN ({marks})",
        list(PART_PHRASES),
    ).fetchall()
    rng.shuffle(pairs)

    out, seen = [], set()
    n_auto = max(0, args.n - len(EDGE_CASES))
    for i, (sku, partname, year, make, model) in enumerate(pairs):
        if len(out) >= n_auto:
            break
        kind, tpl = TEMPLATES[len(out) % len(TEMPLATES)]
        smodel, dropped = shopper_model(model)
        if kind == "alias":
            if (make, model) not in MODEL_ALIASES:
                continue
            smodel, dropped = rng.choice(MODEL_ALIASES[(make, model)]), False
            if (make, model) in (("Ram", "1500 DS"), ("Ram", "1500 DT")):
                dropped = True
        smake = rng.choice(MAKE_ALIASES.get(make, [make.lower()]))
        if model.lower().startswith(make.lower()):  # e.g. "Ram 1500" style names
            smake = ""
        if smodel.isdigit():  # "1500" alone needs the make
            smodel = f"{make.lower()} {smodel}"
            smake = ""
        typo_model = " ".join(typo(w, rng) for w in smodel.split()) if kind == "typo" else smodel
        if kind == "typo" and typo_model == smodel:
            continue
        part = rng.choice(PART_PHRASES[partname])
        q = tpl.format(year=year, yy=f"{year % 100:02d}", make=smake, model=smodel, part=part,
                       alias=smodel, typo_model=typo_model)
        q = " ".join(q.split())
        if q in seen:
            continue
        seen.add(q)
        has_year = "{year}" in tpl or "{yy}" in tpl
        acceptable_parts = sorted({pn for pn, phrases in PART_PHRASES.items() if part in phrases})
        out.append({
            "id": f"g{len(out):04d}", "query": q, "type": kind,
            "year": year if has_year else None,
            "vehicles": acceptable_vehicles(con, make, model, year if has_year else None, dropped),
            "parts": acceptable_parts, "source_sku": sku,
        })

    for j, e in enumerate(EDGE_CASES):
        out.append({"id": f"e{j:03d}", **e})

    OUT.write_text("\n".join(json.dumps(r) for r in out) + "\n")
    by_type: dict[str, int] = {}
    for r in out:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    print(f"wrote {len(out)} goldens -> {OUT}\n{json.dumps(by_type, indent=1)}")


if __name__ == "__main__":
    main()

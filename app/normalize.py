"""Deterministic query normalizer + candidate generator (stage 1, shared by every engine).

Turns "99 ford bronco oil filter" into candidate years / vehicles / part types. The model
interpreters (Haiku, Jev) choose among these candidates; the rules fast-path uses them directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .catalog import Catalog

MAKE_ALIASES = {
    "chevy": "Chevrolet", "chev": "Chevrolet", "chevrolet": "Chevrolet", "caddy": "Cadillac",
    "vw": None, "alfa": "Alfa Romeo", "romeo": "Alfa Romeo", "mopar": None,
}

# Shopper phrase -> (make, model) list. Generated phrases cover most cases; these are the ones
# that can't be derived from model names.
MODEL_ALIASES: dict[str, list[tuple[str, str]]] = {
    "f150": [("Ford", "F-150")], "f 150": [("Ford", "F-150")],
    "f250": [("Ford", "Super Duty")], "f350": [("Ford", "Super Duty")], "f450": [("Ford", "Super Duty")],
    "f 250": [("Ford", "Super Duty")], "f 350": [("Ford", "Super Duty")],
    "raptor": [("Ford", "F-150 Raptor"), ("Ford", "Bronco Raptor"), ("Ford", "Ranger Raptor")],
    "lightning": [("Ford", "F-150 Lightning")],
    "mach e": [("Ford", "Mustang Mach-E")], "mache": [("Ford", "Mustang Mach-E")],
    "stang": [("Ford", "Mustang")],
    "vette": [("Chevrolet", "Corvette")],
    "jl": [("Jeep", "Wrangler JL")], "jk": [("Jeep", "Wrangler JK")], "jlu": [("Jeep", "Wrangler JL")],
    "jku": [("Jeep", "Wrangler JK")], "jt": [("Jeep", "Gladiator")],
    "wk2": [("Jeep", "Grand Cherokee WK")], "gc": [("Jeep", "Grand Cherokee WK")],
    "ram 1500": [("Ram", "1500 DS"), ("Ram", "1500 DT")], "ram1500": [("Ram", "1500 DS"), ("Ram", "1500 DT")],
    "ram 2500": [("Ram", "2500")], "ram 3500": [("Ram", "3500")],
    "silverado": [("Chevrolet", "Silverado 1500")], "sierra": [("GMC", "Sierra 1500")],
    "hellcat": [("Dodge", "Challenger"), ("Dodge", "Charger")],
    "sti": [("Subaru", "STI")], "wrx sti": [("Subaru", "STI")],
}

# Shopper phrase -> partname(s). Fuzzy matching against the 783 partnames handles the rest.
PART_SYNONYMS: dict[str, list[str]] = {
    "oil filter": ["Oil Filter"],
    "air filter": ["Engine Air Filter"], "engine filter": ["Engine Air Filter"],
    "cabin filter": ["Cabin Air Filter"], "cabin air filter": ["Cabin Air Filter"],
    "floor mats": ["All Weather Floor Mats", "Carpeted Floor Mats"],
    "floor mat": ["All Weather Floor Mats", "Carpeted Floor Mats"], "mats": ["All Weather Floor Mats", "Carpeted Floor Mats"],
    "weathertech": ["All Weather Floor Mats"], "floor liners": ["All Weather Floor Mats"],
    "wipers": ["Wiper Blade"], "wiper": ["Wiper Blade"], "wiper blades": ["Wiper Blade"],
    "mud flaps": ["Splash Guards"], "mudflaps": ["Splash Guards"], "mud guards": ["Splash Guards"],
    "bed cover": ["Tonneau Cover"], "tonneau": ["Tonneau Cover"],
    "running boards": ["Step Bars"], "side steps": ["Step Bars"], "nerf bars": ["Step Bars"],
    "bug deflector": ["Air Deflector"], "bug shield": ["Air Deflector"], "vent visors": ["Air Deflector"],
    "rain guards": ["Air Deflector"], "hitch": ["Hitch Receiver"], "trailer hitch": ["Hitch Receiver"],
    "spark plugs": ["Spark Plug"], "plugs": ["Spark Plug"], "brake pads": ["Brake Pads"],
    "rims": ["Wheel"], "wheels": ["Wheel"], "seat covers": ["Seat Cover"], "car cover": ["Car Cover"],
    "remote start": ["Remote Start"], "roof rack": ["Roof Rack"], "fog lights": ["Fog Light"],
    "exhaust": ["Exhaust System"], "cat back": ["Exhaust System"], "catback": ["Exhaust System"],
    "cold air intake": ["Cold Air Intake"], "intake": ["Cold Air Intake"], "lift kit": ["Lift Kit"],
}

STOPWORDS = {"for", "a", "an", "the", "my", "to", "fits", "fit", "on", "with", "and", "of", "in", "parts", "part",
             "looking", "need", "needs", "want", "new", "what", "which", "do", "does", "you", "have", "i", "is", "that"}
SKU_RE = re.compile(r"^(?=.*\d)[a-z0-9\-]{5,}$", re.I)


def key(s: str) -> str:
    """Canonical comparison form: lowercase, hyphens dropped, other punctuation -> space."""
    s = s.lower().replace("-", "").replace("'", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def singular(w: str) -> str:
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


@dataclass
class Candidates:
    query: str
    tokens: list[str]
    years: list[int] = field(default_factory=list)
    makes: list[str] = field(default_factory=list)
    vehicles: list[tuple[str, str]] = field(default_factory=list)  # (make, model)
    vehicle_exact: list[tuple[str, str]] = field(default_factory=list)  # full-name matches
    parts: list[str] = field(default_factory=list)  # shortlist for the interpreter
    part_hits: list[str] = field(default_factory=list)  # strong (synonym/exact) part matches
    skus: list[str] = field(default_factory=list)
    residual: str = ""  # keywords left after removing vehicle/year tokens
    vehicles_wide: list[tuple[str, str]] = field(default_factory=list)  # + sibling models and fuzzy matches
    fuzzy_used: bool = False


class Normalizer:
    def __init__(self, cat: Catalog):
        self.cat = cat
        self.makes = sorted({mk for mk, _ in cat.models})
        self.make_keys = {key(mk): mk for mk in self.makes} | {
            k: v for k, v in MAKE_ALIASES.items() if v
        }
        self.phrases: dict[str, set[tuple[str, str]]] = {}
        self.full_names: dict[str, tuple[str, str]] = {}
        generic = {"series", "van", "sport", "hd", "cab", "connect", "crew", "1500", "2500", "3500", "4500", "5500"}
        for mk, md in cat.models:
            k = key(md)
            self.full_names[k] = self.full_names[f"{key(mk)} {k}"] = (mk, md)
            self._add(k, (mk, md))
            self._add(f"{key(mk)} {k}", (mk, md))
            words = k.split()
            for w in words:
                if w not in generic and not w.isdigit():
                    self._add(w, (mk, md))
            if len(words) == 2:  # "wrangler jl" -> also "jl wrangler"
                self._add(f"{words[1]} {words[0]}", (mk, md))
            for n in range(2, len(words)):  # "grand cherokee wk" -> "grand cherokee", "cherokee wk"
                for i in range(len(words) - n + 1):
                    self._add(" ".join(words[i:i + n]), (mk, md))
        # Aliases override generated phrases ("silverado" alone means the 1500, not every Silverado).
        for phrase, vs in MODEL_ALIASES.items():
            known = {v for v in vs if v in cat.models}
            if known:
                self.phrases[phrase] = known
        self.single_words = [p for p in self.phrases if " " not in p and len(p) >= 4]
        self.part_keys: dict[str, list[str]] = {}
        for p in cat.partnames:
            self.part_keys.setdefault(singular_key(p), []).append(p)
        self.part_list = list(cat.partnames)

    def _add(self, phrase: str, v: tuple[str, str]):
        self.phrases.setdefault(phrase, set()).add(v)

    def run(self, query: str) -> Candidates:
        q = key(query)
        toks = q.split()
        c = Candidates(query=query, tokens=toks)
        used = [False] * len(toks)

        # Years: 1999, 99, '99 (apostrophe already split off by key()).
        for i, t in enumerate(toks):
            if re.fullmatch(r"(19[5-9]\d|20[0-3]\d)", t):
                c.years.append(int(t)); used[i] = True
            elif re.fullmatch(r"\d{2}", t):
                y = int(t)
                c.years.append(1900 + y if y >= 50 else 2000 + y); used[i] = True
        # "2019-2021" style ranges collapse to the first year; key() removed the hyphen -> 8 digits.
        for i, t in enumerate(toks):
            if re.fullmatch(r"(19|20)\d{2}(19|20)\d{2}", t):
                c.years.append(int(t[:4])); used[i] = True

        # Vehicles first (so "ram 1500" isn't eaten as make "Ram"): longest phrase wins, 3..1 tokens.
        found: set[tuple[str, str]] = set()
        spans: list[tuple[range, set[tuple[str, str]]]] = []
        for n in (3, 2, 1):
            for i in range(len(toks) - n + 1):
                if any(used[i:i + n]):
                    continue
                ph = " ".join(toks[i:i + n])
                if ph in self.phrases:
                    found |= self.phrases[ph]
                    spans.append((range(i, i + n), self.phrases[ph]))
                    if ph in self.full_names:
                        c.vehicle_exact.append(self.full_names[ph])
                    for j in range(i, i + n):
                        used[j] = True
                    if " " in ph and ph.split()[0] in self.make_keys:
                        c.makes.append(self.make_keys[ph.split()[0]])

        # Makes on whatever is left.
        for i, t in enumerate(toks):
            if used[i]:
                continue
            if t in self.make_keys:
                c.makes.append(self.make_keys[t]); used[i] = True
            elif i + 1 < len(toks) and f"{t} {toks[i+1]}" in self.make_keys:
                c.makes.append(self.make_keys[f"{t} {toks[i+1]}"]); used[i] = used[i + 1] = True
        c.makes = list(dict.fromkeys(c.makes))

        # Typo tolerance on leftover tokens ("silverdo", "broncoo").
        if not found:
            for i, t in enumerate(toks):
                if used[i] or len(t) < 4 or t in STOPWORDS:
                    continue
                hit = process.extractOne(t, self.single_words, scorer=fuzz.ratio, score_cutoff=82)
                if hit:
                    found |= self.phrases[hit[0]]
                    used[i] = True
                    c.fuzzy_used = True
        if c.makes:
            narrowed = {v for v in found if v[0] in c.makes}
            found = narrowed or found
            # "dodge challenger spark plug": "spark" matched Chevrolet Spark - give the token back to the part.
            for span, vs in spans:
                if not (vs & found):
                    for j in span:
                        used[j] = False
            c.vehicle_exact = [v for v in c.vehicle_exact if v in found]
        c.vehicles = sorted(found)

        # SKUs / part numbers.
        for i, t in enumerate(toks):
            if not used[i] and SKU_RE.match(t) and self.cat.has_sku(t):
                c.skus.append(self.cat.canonical_sku(t))
                used[i] = True

        residual_toks = [t for i, t in enumerate(toks) if not used[i] and t not in STOPWORDS]
        c.residual = " ".join(residual_toks)

        # Part types: synonyms, exact partname, then fuzzy shortlist.
        r = c.residual
        hits: list[str] = []
        for syn, pns in sorted(PART_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"\b{re.escape(syn)}\b", r):
                hits += [p for p in pns if p in self.cat.partnames]
        words = [singular(w) for w in r.split()]
        for n in range(len(words), 0, -1):  # longest sub-phrase that is a partname ("rear spoiler" -> Spoiler)
            subhits = [p for i in range(len(words) - n + 1) for p in self.part_keys.get(" ".join(words[i:i + n]), [])]
            if subhits:
                if not hits or n > 1:
                    hits += subhits
                break
        c.part_hits = list(dict.fromkeys(hits))
        shortlist = list(c.part_hits)
        if r:
            for name, score, _ in process.extract(r, self.part_list, scorer=fuzz.WRatio, limit=40):
                if score >= 50 and name not in shortlist:
                    shortlist.append(name)
        c.parts = shortlist[:40]
        c.vehicles_wide = self._widen(c)
        return c

    def keywords_for(self, c: Candidates, vehicles: list[tuple[str, str]]) -> str:
        """Search keywords once the vehicle is decided: every typed word except years, part numbers, stopwords,
        make names and words that belong to the chosen vehicle (including typo'd forms like 'ratpor').
        Words wrongly claimed by a vehicle that wasn't chosen ('spark' -> Chevrolet Spark) come back."""
        if not vehicles or len(vehicles) > 4:  # make-only ("chevy floor mats"): too many model words to strip safely
            return c.residual
        vwords = {w for mk, md in vehicles for w in key(f"{mk} {md}").split()}
        for phrase, vs in self.phrases.items():  # aliases of the chosen vehicles ("f150", "jl", "ram 1500")
            if any(v in vs for v in vehicles):
                vwords |= set(phrase.split())
        skus = {s.lower() for s in c.skus}
        out = []
        for t in c.tokens:
            if (t in STOPWORDS or t in self.make_keys or t in vwords or t.lower() in skus
                    or re.fullmatch(r"(19|20)?\d{2}((19|20)\d{2})?", t)):
                continue
            if len(t) >= 4 and vwords and process.extractOne(t, list(vwords), scorer=fuzz.ratio, score_cutoff=75):
                continue  # typo of a vehicle word
            out.append(t)
        return " ".join(out)

    def _widen(self, c: Candidates) -> list[tuple[str, str]]:
        """A wider vehicle option list for classifier-style interpreters, which can only pick what they're offered:
        sibling trims ("F-150" -> "F-150 Raptor", "F-150 Lightning") and fuzzy matches for leftover words
        ("ratpor" -> Raptor), kept to the makes already in play."""
        wide = set(c.vehicles)
        for mk, md in c.vehicles:
            wide |= {(m, d) for m, d in self.cat.models if m == mk and d.startswith(md + " ")}
        makes = {mk for mk, _ in wide} | set(c.makes)
        for t in c.residual.split():
            if len(t) < 4 or t in STOPWORDS:
                continue
            hit = process.extractOne(t, self.single_words, scorer=fuzz.ratio, score_cutoff=75)
            if hit:
                wide |= {v for v in self.phrases[hit[0]] if not makes or v[0] in makes}
        return sorted(wide)


def singular_key(s: str) -> str:
    return " ".join(singular(w) for w in key(s).split())


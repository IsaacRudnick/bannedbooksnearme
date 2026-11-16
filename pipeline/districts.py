"""School district names: read the NCES list, then match ban-source names to LEAIDs.

Reads (extract step):
    raw/nces/EDGE_SCHOOLDISTRICT_TL25_SY2425.zip  (only the .dbf attribute table, never the 312 MB shapefile)
Writes (extract step):
    cache/nces_districts.csv   leaid, state, name, lograde, higrade, funcstat, parent_leaid
Reads (match step):
    cache/nces_districts.csv   and a list of (state, district name) pairs from the ban sources
Writes (match step):
    review/unmatched_districts.csv   the pairs that got no LEAID, so a person can check them

Why a matcher: PEN America and Dr. Magnusson write district names in their own words
("Katy ISD", "School District of Manatee County"). NCES writes its own words
("Katy Independent School District", "Manatee County School District"). The site needs the
LEAID to join a ban to the districts that cover a ZIP code. A wrong match puts a ban in the
wrong place, so the matcher prefers no match over a doubtful match.
"""
import csv
import re
import struct
import zipfile
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

from pipeline.normalize import STATES, fold

# NCES uses a state FIPS code as the first two digits of every LEAID.
FIPS_TO_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY",
    "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI",
}

# TODO: tune both numbers after reviewing review/unmatched_districts.csv.
FUZZY_MIN_SCORE = 94      # the best candidate must score at least this (0 to 100). 92 let "Southwest Valley" match "Southeast Valley".
FUZZY_MIN_MARGIN = 5      # and beat the second-best candidate by at least this many points

NCES_COLUMNS = ["leaid", "state", "name", "lograde", "higrade", "funcstat", "parent_leaid"]
DBF_NAME = "EDGE_SCHOOLDISTRICT_TL25_SY2425.dbf"


# ---------------------------------------------------------------- reading the DBF

def read_dbf(path):
    """Return the rows of a dBase III file as a list of dicts of stripped strings.

    The file has a 32-byte header, then 32 bytes per field, then fixed-width records.
    """
    data = Path(path).read_bytes()
    n_rows, header_len, row_len = struct.unpack("<IHH", data[4:12])
    fields, offset, pos = [], 32, 1          # pos 0 of each record is the "deleted" flag
    while data[offset] != 0x0D:
        name = data[offset:offset + 11].split(b"\0")[0].decode("ascii")
        length = data[offset + 16]
        fields.append((name, pos, length))
        pos += length
        offset += 32
    rows = []
    for i in range(n_rows):
        rec = data[header_len + i * row_len: header_len + (i + 1) * row_len]
        rows.append({name: rec[a:a + n].decode("utf-8", "replace").strip() for name, a, n in fields})
    return rows


def _extract_member(zip_path, suffix, dest_dir):
    """Extract the one zip member whose name ends in suffix. Return its path on disk."""
    dest_dir = Path(dest_dir)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith(suffix):
                target = (dest_dir / name).resolve()
                if dest_dir.resolve() not in target.parents:
                    raise ValueError(f"unsafe path in zip: {name}")
                if not target.exists():
                    zf.extract(name, dest_dir)
                return target
    raise FileNotFoundError(f"{suffix} not found in {zip_path}")


# ---------------------------------------------------------------- extract step

_BASE_SUFFIX = re.compile(r"\s*\(\d+-\d+\)")
_IN_FOR_SUFFIX = re.compile(r"\s+(in|for)\s+.*$")


def _parent_name(name):
    """Return the name of the real district a 'fictitious' NCES row stands for.

    NCES adds fictitious rows for the part of a district that serves only some grades, such as
    'Hallettsville Independent School District (9-12) in Vysehrad'. The real district is
    'Hallettsville Independent School District'.
    """
    base = _BASE_SUFFIX.sub("", name)
    return _IN_FOR_SUFFIX.sub("", base).strip()


def build_nces_rows(dbf_rows):
    """Return the nces_districts.csv rows as dicts.

    Names come from the DBF, which holds correct UTF-8. The GRF xlsx file holds the same
    names with accented letters damaged ('PeÃ±asco'), so the pipeline does not use them.
    """
    rows = []
    for r in dbf_rows:
        leaid = r["GEOID"]
        rows.append({
            "leaid": leaid, "state": FIPS_TO_STATE.get(r["STATEFP"], ""),
            "name": r["NAME"],
            "lograde": r["LOGRADE"], "higrade": r["HIGRADE"],
            "funcstat": r["FUNCSTAT"], "parent_leaid": "",
        })
    real = defaultdict(list)
    for r in rows:
        if r["funcstat"] == "E":
            real[(r["state"], r["name"])].append(r["leaid"])
    for r in rows:
        if r["funcstat"] != "E":
            parents = real.get((r["state"], _parent_name(r["name"])), [])
            if len(parents) == 1:               # link only when exactly one real district fits
                r["parent_leaid"] = parents[0]
    return sorted(rows, key=lambda r: r["leaid"])


def extract(raw_dir=Path("raw"), cache_dir=Path("cache")):
    """Write cache/nces_districts.csv from the NCES district boundary DBF."""
    raw_dir, cache_dir = Path(raw_dir), Path(cache_dir)
    out = cache_dir / "nces_districts.csv"
    zip_path = raw_dir / "nces" / "EDGE_SCHOOLDISTRICT_TL25_SY2425.zip"
    if out.exists() and not zip_path.exists():
        return out                                # the cache is enough
    dbf = _extract_member(zip_path, DBF_NAME, raw_dir / "nces" / "extracted")
    rows = build_nces_rows(read_dbf(dbf))
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=NCES_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return out


# ---------------------------------------------------------------- name normalization

# Each pattern is a phrase that means the same thing in two sources. Longest phrases go first.
# Both the ban name and the NCES name go through the same rules, so only consistency matters.
_PHRASES = [
    (r"consolidated independent school district|consolidated isd|cisd", "cisd"),
    (r"independent school district|isd", "isd"),
    (r"community unit school district|csud|cusd", "cusd"),
    (r"community school district|csd", "csd"),
    (r"unified school district|unified schools|usd|unified", "usd"),
    (r"school administrative district|msad|sad", "sad"),
    (r"public school district|public school system|school system|school district|district schools|"
     r"school board|school department|board of education|public schools|schools|school|sd", "sd"),
    (r"parish|borough", "county"),
    (r"st", "saint"), (r"mt", "mount"), (r"ft", "fort"),
]
_PHRASE_RES = [(re.compile(rf"(?<![a-z0-9])(?:{p})(?![a-z0-9])"), to) for p, to in _PHRASES]
_DISTRICT_OF = re.compile(r"^(?:school district|public schools|schools) of (.+)$")
_STATE_NAMES = sorted((fold(n) for n in STATES), key=len, reverse=True)
_NUMBER_WORDS = re.compile(r"(?<![a-z0-9])(?:no|number|nbr)(?![a-z0-9])")
_TYPE_WORDS = {"isd", "cisd", "usd", "cusd", "csd", "sd", "county", "the", "of",
               "city", "community", "unit", "joint", "local", "district"}
_NUMBER_NAMES = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                 "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_NUMBER_NAME_RE = re.compile(r"\b(sd|sad) (" + "|".join(_NUMBER_NAMES) + r")\b")
_LEADING_ZEROS = re.compile(r"(?<![a-z0-9])0+(?=\d)")
_ROMAN = re.compile(r"[ivx]+")


def _strip_trailing_state(text):
    """Remove a state name at the end: 'mason city schools iowa' becomes 'mason city schools'."""
    for name in _STATE_NAMES:
        if text.endswith(" " + name):
            return text[: -len(name) - 1]
    return text


def normalize_name(name, state=""):
    """Return one comparable form of a district name. See the module docstring for why.

    state is the two-letter code of the name's state. It lets the function drop a trailing
    code such as the ", ID" in "Lakeland Joint School District 272, ID".
    """
    text = fold(name)
    if state and text.endswith(" " + state.lower()):
        text = text[: -len(state) - 1]
    text = _NUMBER_WORDS.sub(" ", text)
    text = _strip_trailing_state(text)
    text = re.sub(r"^the ", "", text)
    # "School District of Manatee County" is the Florida form of "Manatee County School District".
    text = _DISTRICT_OF.sub(r"\1 school district", text)
    for pattern, to in _PHRASE_RES:
        text = pattern.sub(to, text)
    text = _NUMBER_NAME_RE.sub(lambda m: m.group(1) + " " + _NUMBER_NAMES[m.group(2)], text)   # "district two" -> "district 2"
    text = _LEADING_ZEROS.sub("", text)                                     # "district 06" -> "district 6"
    tokens = text.split()
    deduped = [t for i, t in enumerate(tokens) if not (i and t == tokens[i - 1])]
    return " ".join(deduped)


def core_name(norm):
    """Return the normalized name without district-type words: 'katy isd' becomes 'katy'."""
    return " ".join(t for t in norm.split() if t not in _TYPE_WORDS)


def bare_name(norm):
    """Return core_name without district numbers, roman numerals and one-letter codes.

    'wentzville r iv sd' and 'natrona county sd 1' lose their codes, so the plain name
    'wentzville' or 'natrona' can match them when it is unique in the state.
    """
    return " ".join(t for t in core_name(norm).split()
                    if not (t.isdigit() or _ROMAN.fullmatch(t) or len(t) == 1))


# ---------------------------------------------------------------- matching

class _Index:
    """The NCES districts of all states, with lookups by exact name and by core name."""

    def __init__(self, nces_rows):
        self.by_state = defaultdict(list)       # state -> [(leaid, name, norm, core)]
        for r in nces_rows:
            if r["funcstat"] == "E":            # fictitious rows are not real districts
                norm = normalize_name(r["name"], r["state"])
                self.by_state[r["state"]].append((r["leaid"], r["name"], norm, core_name(norm)))
        self.exact = defaultdict(lambda: defaultdict(set))
        self.core = defaultdict(lambda: defaultdict(set))
        self.bare = defaultdict(lambda: defaultdict(set))
        self.norm = {}
        for state, items in self.by_state.items():
            for leaid, _name, norm, core in items:
                self.norm[leaid] = norm
                self.exact[state][norm].add(leaid)
                if core:
                    # Spaces are dropped from the core key, so "DeSoto" and "De Soto" match.
                    self.core[state][core.replace(" ", "")].add(leaid)
                if bare_name(norm):
                    self.bare[state][bare_name(norm)].add(leaid)
        self.names = {r["leaid"]: r["name"] for r in nces_rows}


def _digits(text):
    return sorted(re.findall(r"\d+", text))


def _fuzzy(index, state, norm):
    """Return (accepted, best_leaid, best_score) from rapidfuzz inside one state."""
    items = index.by_state.get(state, [])
    if not items:
        return False, "", 0.0
    top = process.extract(norm, [i[2] for i in items], scorer=fuzz.token_sort_ratio, limit=3)
    best_norm, best_score, best_i = top[0]
    best = items[best_i]
    # The second-best must be a different district, not a duplicate of the same name.
    others = [t[1] for t in top[1:] if items[t[2]][0] != best[0]]
    second = others[0] if others else 0.0
    accepted = (
        best_score >= FUZZY_MIN_SCORE
        and best_score - second >= FUZZY_MIN_MARGIN
        and _digits(best_norm) == _digits(norm)     # "district 12" must never match "district 120"
    )
    return accepted, best[0], best_score


def _match_one(index, state, name):
    """Return the match dict for one pair."""
    norm = normalize_name(name, state)
    exact = index.exact.get(state, {}).get(norm, set())
    if len(exact) == 1:
        leaid = next(iter(exact))
        return {"leaid": leaid, "nces_name": index.names[leaid], "method": "exact", "score": 100}
    core = core_name(norm)
    cores = index.core.get(state, {}).get(core.replace(" ", ""), set()) if core else set()
    if len(cores) == 1:
        leaid = next(iter(cores))
        return {"leaid": leaid, "nces_name": index.names[leaid], "method": "core", "score": 100}
    bare = bare_name(norm)
    bares = index.bare.get(state, {}).get(bare, set()) if bare else set()
    if len(bares) == 1:
        leaid = next(iter(bares))
        have, want = _digits(norm), _digits(index.norm[leaid])
        # Two different district numbers mean two different districts, so refuse that match.
        if not (have and want and have != want):
            return {"leaid": leaid, "nces_name": index.names[leaid], "method": "bare", "score": 100}
    accepted, leaid, score = _fuzzy(index, state, norm)
    if accepted:
        return {"leaid": leaid, "nces_name": index.names[leaid], "method": "fuzzy", "score": round(score, 1)}
    return {"leaid": "", "nces_name": index.names.get(leaid, ""), "method": "none",
            "score": round(score, 1), "best_leaid": leaid}


def read_nces_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def match_districts(pairs, nces_csv=Path("cache/nces_districts.csv"),
                    review_path=Path("review/unmatched_districts.csv"), counts=None):
    """Match (state, raw name) pairs to NCES districts. Return {pair: match dict}.

    A match dict has leaid, nces_name, method (exact, core, bare, fuzzy, none) and score.
    Unmatched pairs have leaid "" and go to review_path. Print the match rate.
    """
    index = _Index(read_nces_csv(nces_csv))
    counts = counts or {}
    unique = list(dict.fromkeys(pairs))
    result = {pair: _match_one(index, *pair) for pair in unique}
    _write_review(result, counts, review_path)
    _print_rates(result, counts)
    return {p: {k: m[k] for k in ("leaid", "nces_name", "method", "score")} for p, m in result.items()}


def _write_review(result, counts, review_path):
    review_path = Path(review_path)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [(state, name, m) for (state, name), m in result.items() if not m["leaid"]]
    rows.sort(key=lambda r: (-counts.get((r[0], r[1]), 0), r[0], r[1]))
    with open(review_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["state", "name", "best_candidate", "best_leaid", "score", "ban_records"])
        for state, name, m in rows:
            w.writerow([state, name, m["nces_name"], m.get("best_leaid", ""), m["score"],
                        counts.get((state, name), 0)])


def _print_rates(result, counts):
    total = len(result)
    hit = sum(1 for m in result.values() if m["leaid"])
    weight = sum(counts.get(p, 1) for p in result)
    weight_hit = sum(counts.get(p, 1) for p, m in result.items() if m["leaid"])
    print(f"district match: {hit}/{total} unique names ({_pct(hit, total)}), "
          f"{weight_hit}/{weight} ban records ({_pct(weight_hit, weight)})")


def _pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"

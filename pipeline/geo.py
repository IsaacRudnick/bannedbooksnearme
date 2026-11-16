"""Geography: turn raw NCES, Census and IMLS files into small cache CSVs, then into ZIP files.

Reads (extract step):
    raw/nces/GRF25.zip                  school district to ZCTA relationships (the .xlsx table)
    raw/census/2025_Gaz_zcta_national.zip   ZCTA centroids
    raw/imls/pls_fy2024_csv.zip         public library outlets
    (the district names come from pipeline/districts.py)
Writes (extract step):
    cache/zcta_districts.csv    zcta, leaid, land_sqmi, share
    cache/zcta_centroids.csv    zcta, lat, lon
    cache/library_outlets.csv   id, name, address, city, state, zip, lat, lon, type
    cache/nces_districts.csv    (written by pipeline/districts.py)
Reads (ZIP step):  the four cache CSVs above.
Writes (ZIP step): data/zips/NNN.json, one file per first-three-digits group.

Why: the page gets a ZIP code from the visitor. It needs the districts that cover the ZIP and
the 3 nearest public libraries. The build does this work once, so the page only fetches one
small file.
"""
import csv
import json
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path

from pipeline import districts

# GRF rows with a very small overlap are map-edge noise, not real coverage.
# TODO: tune after checking sample ZIPs. Replace with a rule from real boundary data if one exists.
MIN_DISTRICT_SHARE = 0.01     # drop a district that covers less than 1% of the ZIP's land
LIBRARY_COUNT = 3
GRID_DEGREES = 0.5            # TODO: tune if the nearest-library search is slow (smaller cell) or ring-heavy (larger cell).
EARTH_RADIUS_MILES = 3958.8
MILES_PER_DEGREE = 69.0

# IMLS outlet type codes. CE = central library, BR = branch. BS = bookmobile, BM = books-by-mail.
KEPT_OUTLET_TYPES = {"CE", "BR"}
TEMPORARILY_CLOSED = "23"     # IMLS STATSTRU code: the outlet was closed for the whole year

ZCTA_DISTRICT_COLUMNS = ["zcta", "leaid", "land_sqmi", "share"]
CENTROID_COLUMNS = ["zcta", "lat", "lon"]
LIBRARY_COLUMNS = ["id", "name", "address", "city", "state", "zip", "lat", "lon", "type"]


# ---------------------------------------------------------------- small helpers

def _write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _extract_member(zip_path, suffix, dest_dir):
    """Extract one zip member by name ending. Refuse paths that leave dest_dir."""
    return districts._extract_member(zip_path, suffix, dest_dir)


_MC_PREFIX = re.compile(r"^(Mc)([a-z])")
_KEEP_UPPER = {"NE", "NW", "SE", "SW", "PO", "US", "II", "III", "IV", "USA", "LLC"}
_SMALL_WORDS = {"of", "the", "and", "for", "at", "in", "on", "a"}


def smart_title(text):
    """Turn 'ANCHOR POINT PUBLIC LIBRARY' into 'Anchor Point Public Library'.

    IMLS writes names in capitals. Mixed case reads better on the page.
    """
    words = []
    for i, word in enumerate(text.split()):
        if word in _KEEP_UPPER:
            words.append(word)
        elif any(c.isdigit() for c in word):
            words.append(word.lower() if re.fullmatch(r"\d+(ST|ND|RD|TH)", word) else word)
        elif word.lower() in _SMALL_WORDS and i:
            words.append(word.lower())
        else:
            words.append(_cap_word(word))
    return " ".join(words)


def _cap_word(word):
    """Capitalize each part of a word split by hyphen or slash. Keep 's after an apostrophe small."""
    parts = re.split(r"([-/])", word.lower())
    out = []
    for p in parts:
        if "'" in p:
            head, _, tail = p.partition("'")
            tail = tail if tail == "s" else tail.capitalize() if len(head) == 1 else tail
            p = head.capitalize() + "'" + tail
        else:
            p = p.capitalize()
        out.append(_MC_PREFIX.sub(lambda m: "Mc" + m.group(2).upper(), p))
    return "".join(out)


# ---------------------------------------------------------------- extract step

def read_grf(raw_dir):
    """Return GRF rows as (zcta, leaid, land_sqmi). The GRF names are not used: see districts.build_nces_rows."""
    import openpyxl            # only the extract step needs it

    path = _extract_member(raw_dir / "nces" / "GRF25.zip", "grf25_lea_zcta5ce20.xlsx",
                           raw_dir / "nces" / "extracted")
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = []
    for i, (leaid, _name, zcta, _count, land, _water) in enumerate(wb.active.iter_rows(values_only=True)):
        if i == 0:
            continue                                  # header row
        if str(zcta) != "00000":                      # 00000 means "area outside every ZCTA"
            rows.append((str(zcta), str(leaid), float(land)))
    wb.close()
    return rows


def merge_zcta_districts(rows, parent_of, nces):
    """Return cache rows [zcta, leaid, land_sqmi, share].

    Two clean-up steps:
    1. A fictitious NCES row (one grade band of a real district) turns into its real district.
    2. NCES rows named 'School District Not Defined' drop out. They are not districts.
    """
    area = defaultdict(float)
    for zcta, leaid, land in rows:
        leaid = parent_of.get(leaid, leaid)
        if nces.get(leaid, {}).get("name") == "School District Not Defined":
            continue
        area[(zcta, leaid)] += land
    total = defaultdict(float)
    for (zcta, _), land in area.items():
        total[zcta] += land
    out = []
    for (zcta, leaid), land in sorted(area.items()):
        share = land / total[zcta] if total[zcta] else 1.0
        out.append([zcta, leaid, f"{land:.4f}", f"{share:.4f}"])
    return out


def read_centroids(raw_dir):
    path = _extract_member(raw_dir / "census" / "2025_Gaz_zcta_national.zip",
                           "2025_Gaz_zcta_national.txt", raw_dir / "census" / "extracted")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="|"):
            r = {k.strip(): v.strip() for k, v in r.items()}
            rows.append([r["GEOID"], f"{float(r['INTPTLAT']):.5f}", f"{float(r['INTPTLONG']):.5f}"])
    return sorted(rows)


def read_outlets(raw_dir):
    """Return library outlet rows. Keep central and branch outlets that are open and have a location."""
    path = _extract_member(raw_dir / "imls" / "pls_fy2024_csv.zip", "pls_fy24_outlet_pud24i.csv",
                           raw_dir / "imls" / "extracted")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")                   # the file has accented letters in old encoding
    rows = []
    for r in csv.DictReader(text.splitlines()):
        if r["C_OUT_TY"] not in KEPT_OUTLET_TYPES or r["STATSTRU"] == TEMPORARILY_CLOSED:
            continue
        lat, lon = float(r["LATITUDE"] or 0), float(r["LONGITUD"] or 0)
        if not lat or not lon:                        # a missing location is 0 or negative -9 style flag
            continue
        rows.append([f"{r['FSCSKEY']}-{r['FSCS_SEQ']}", smart_title(r["LIBNAME"]), smart_title(r["ADDRESS"]),
                     smart_title(r["CITY"]), r["STABR"], r["ZIP"][:5],
                     f"{lat:.5f}", f"{lon:.5f}", r["C_OUT_TY"]])
    return sorted(rows)


def extract(raw_dir=Path("raw"), cache_dir=Path("cache")):
    """Write the four cache CSVs from raw/. Skip a file when its raw input is gone but the cache exists."""
    raw_dir, cache_dir = Path(raw_dir), Path(cache_dir)
    nces_csv = cache_dir / "nces_districts.csv"
    zd_csv = cache_dir / "zcta_districts.csv"
    if not (zd_csv.exists() and nces_csv.exists() and not (raw_dir / "nces" / "GRF25.zip").exists()):
        grf_rows = read_grf(raw_dir)
        districts.extract(raw_dir, cache_dir)
        nces = {r["leaid"]: r for r in districts.read_nces_csv(nces_csv)}
        parent_of = {k: r["parent_leaid"] for k, r in nces.items() if r["parent_leaid"]}
        _write_csv(zd_csv, ZCTA_DISTRICT_COLUMNS, merge_zcta_districts(grf_rows, parent_of, nces))
    jobs = [
        (cache_dir / "zcta_centroids.csv", CENTROID_COLUMNS, raw_dir / "census" / "2025_Gaz_zcta_national.zip", read_centroids),
        (cache_dir / "library_outlets.csv", LIBRARY_COLUMNS, raw_dir / "imls" / "pls_fy2024_csv.zip", read_outlets),
    ]
    for path, columns, zip_path, reader in jobs:
        if path.exists() and not zip_path.exists():
            continue
        _write_csv(path, columns, reader(raw_dir))
    for name in ("nces_districts", "zcta_districts", "zcta_centroids", "library_outlets"):
        p = cache_dir / f"{name}.csv"
        print(f"{p}: {p.stat().st_size / 1e6:.2f} MB")


# ---------------------------------------------------------------- nearest libraries

def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles between two points given in degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


class LibraryGrid:
    """Outlets sorted into lat/lon cells so a search looks only at nearby cells."""

    def __init__(self, outlets, cell=GRID_DEGREES):
        self.cell = cell
        self.cells = defaultdict(list)
        for o in outlets:
            self.cells[self._key(o["lat"], o["lon"])].append(o)
        self.max_ring = int(360 / cell) + 1

    def _key(self, lat, lon):
        return (math.floor(lat / self.cell), math.floor(lon / self.cell))

    def _ring(self, cy, cx, r):
        """Yield the cells at distance r (in cells) from the centre cell."""
        if r == 0:
            yield (cy, cx)
            return
        for dx in range(-r, r + 1):
            yield (cy - r, cx + dx)
            yield (cy + r, cx + dx)
        for dy in range(-r + 1, r):
            yield (cy + dy, cx - r)
            yield (cy + dy, cx + r)

    def _ring_min_miles(self, r, lat):
        """The least distance from the centre point to any cell in ring r, in miles.

        A point can sit anywhere inside its own cell, so ring r is at least r-1 whole cells away.
        Longitude degrees are shorter far from the equator, so use the shorter size to stay safe.
        """
        lat_edge = min(89.0, abs(lat) + r * self.cell)
        return max(0, r - 1) * self.cell * MILES_PER_DEGREE * math.cos(math.radians(lat_edge))

    def nearest(self, lat, lon, count=LIBRARY_COUNT):
        """Return up to count (miles, outlet) pairs, nearest first."""
        cy, cx = self._key(lat, lon)
        found = []
        for r in range(self.max_ring):
            if len(found) >= count and self._ring_min_miles(r, lat) > found[count - 1][0]:
                break
            for key in self._ring(cy, cx, r):
                for o in self.cells.get(key, ()):
                    found.append((haversine_miles(lat, lon, o["lat"], o["lon"]), o))
            found.sort(key=lambda t: (t[0], t[1]["id"]))
            del found[count:]
        return found


# ---------------------------------------------------------------- ZIP files

def load_outlets(path):
    return [{"id": r["id"], "name": r["name"], "address": r["address"], "city": r["city"],
             "state": r["state"], "zip": r["zip"], "lat": float(r["lat"]), "lon": float(r["lon"])}
            for r in _read_csv(path)]


def zcta_districts(zd_rows, nces):
    """Return {zcta: [district dicts]} keeping districts above MIN_DISTRICT_SHARE.

    The largest district of a ZIP always stays, so a ZIP never ends with no district
    only because its land is split into many small pieces.
    """
    by_zcta = defaultdict(list)
    for r in zd_rows:
        if r["leaid"] in nces:
            by_zcta[r["zcta"]].append((float(r["share"]), r["leaid"]))
    out = {}
    for zcta, items in by_zcta.items():
        items.sort(key=lambda t: (-t[0], t[1]))
        kept = [items[0]] + [t for t in items[1:] if t[0] >= MIN_DISTRICT_SHARE]
        out[zcta] = [{"leaid": leaid, "name": nces[leaid]["name"], "state": nces[leaid]["state"]}
                     for _, leaid in sorted(kept, key=lambda t: (nces[t[1]]["state"], nces[t[1]]["name"]))]
    return out


def library_entry(miles, outlet):
    return {"name": outlet["name"], "address": outlet["address"], "city": outlet["city"],
            "state": outlet["state"], "zip": outlet["zip"], "miles": round(miles, 1)}


def build_entries(centroids, zcta_to_districts, grid):
    """Return {zcta: entry} where entry is the dict DESIGN.md describes."""
    entries = {}
    for zcta, (lat, lon) in centroids.items():
        libs = grid.nearest(lat, lon)
        dists = zcta_to_districts.get(zcta, [])
        states = sorted({d["state"] for d in dists if d["state"]})
        if not states and libs:
            # A ZCTA with no district (a PO-box or water ZIP) borrows the state of its nearest library.
            # TODO: confirm with the owner. DESIGN.md says states come only from districts.
            states = [libs[0][1]["state"]]
        entries[zcta] = {"states": states, "districts": dists,
                         "libraries": [library_entry(m, o) for m, o in libs]}
    return entries


def write_zip_files(entries, out_dir):
    """Write data/zips/NNN.json. Return (file count, total bytes, name of the largest file, its bytes)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(dict)
    for zcta in sorted(entries):
        groups[zcta[:3]][zcta] = entries[zcta]
    sizes = {}
    for prefix, group in groups.items():
        text = json.dumps(group, separators=(",", ":"), ensure_ascii=False)
        (out_dir / f"{prefix}.json").write_text(text, encoding="utf-8")
        sizes[f"{prefix}.json"] = len(text.encode("utf-8"))
    biggest = max(sizes, key=sizes.get)
    return len(sizes), sum(sizes.values()), biggest, sizes[biggest]


def build_zip_files(cache_dir=Path("cache"), out_dir=Path("data/zips")):
    """Write data/zips/NNN.json from the cache CSVs."""
    cache_dir = Path(cache_dir)
    nces = {r["leaid"]: r for r in districts.read_nces_csv(cache_dir / "nces_districts.csv")}
    centroids = {r["zcta"]: (float(r["lat"]), float(r["lon"]))
                 for r in _read_csv(cache_dir / "zcta_centroids.csv")}
    zd = zcta_districts(_read_csv(cache_dir / "zcta_districts.csv"), nces)
    grid = LibraryGrid(load_outlets(cache_dir / "library_outlets.csv"))
    entries = build_entries(centroids, zd, grid)
    count, total, biggest, big_bytes = write_zip_files(entries, out_dir)
    print(f"{count} ZIP files, {total / 1e6:.2f} MB total, largest {biggest} ({big_bytes / 1e3:.0f} KB)")
    return entries

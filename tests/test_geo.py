"""Tests for pipeline/geo.py.

The rows in tests/fixtures/geo/ are real: NCES districts, GRF ZCTA overlaps, Census centroids
and IMLS library outlets for ZIP 75001 (Addison, Texas), 84101 (Salt Lake City) and 77442.
"""
import json
from pathlib import Path

import pytest

from pipeline import districts, geo

FIXTURES = Path(__file__).parent / "fixtures" / "geo"


@pytest.fixture(scope="module")
def entries(tmp_path_factory):
    out = tmp_path_factory.mktemp("zips")
    result = geo.build_zip_files(cache_dir=FIXTURES, out_dir=out)
    result["_dir"] = out
    return result


def test_nearest_three_libraries_are_in_distance_order(entries):
    libs = entries["75001"]["libraries"]
    assert [l["name"] for l in libs] == [
        "Farmers Branch Manske Public Library", "Timberglen Branch Library", "Fretz Park Branch Library"]
    assert [l["miles"] for l in libs] == [2.8, 3.0, 3.2]
    assert libs[0] == {"name": "Farmers Branch Manske Public Library", "address": "13613 Webb Chapel Rd",
                       "city": "Farmers Branch", "state": "TX", "zip": "75234", "miles": 2.8}


def test_salt_lake_city_zip(entries):
    e = entries["84101"]
    assert e["states"] == ["UT"]
    assert e["districts"] == [{"leaid": "4900870", "name": "Salt Lake City School District", "state": "UT"}]
    assert e["libraries"][0]["name"] == "Salt Lake City Public Library Main Library"
    assert [l["miles"] for l in e["libraries"]] == sorted(l["miles"] for l in e["libraries"])


def test_zip_with_two_districts_lists_both(entries):
    # 75001 sits in Dallas ISD (94% of its land) and Carrollton-Farmers Branch ISD (6%).
    names = [d["name"] for d in entries["75001"]["districts"]]
    assert names == ["Carrollton-Farmers Branch Independent School District", "Dallas Independent School District"]
    assert entries["75001"]["states"] == ["TX"]


def test_tiny_overlaps_are_dropped_but_the_largest_district_stays(entries):
    # In 77442, Columbus ISD covers 0.3% and another district under 0.01% of the land.
    # Only Rice CISD (99.7%) is left.
    assert [d["leaid"] for d in entries["77442"]["districts"]] == ["4836970"]


def test_zip_files_are_grouped_by_first_three_digits(entries):
    out = entries["_dir"]
    assert sorted(p.name for p in out.glob("*.json")) == ["750.json", "774.json", "841.json"]
    data = json.loads((out / "750.json").read_text(encoding="utf-8"))
    assert list(data) == ["75001"]
    assert set(data["75001"]) == {"states", "districts", "libraries"}


def test_zip_json_is_compact(entries):
    text = (entries["_dir"] / "841.json").read_text(encoding="utf-8")
    assert '": ' not in text and '", "' not in text and '," ' not in text
    assert text.startswith('{"84101":{"states":["UT"]')


def test_haversine_matches_a_known_distance():
    # Dallas City Hall to Fort Worth City Hall is about 30 miles in a straight line.
    miles = geo.haversine_miles(32.7767, -96.7970, 32.7555, -97.3308)
    assert miles == pytest.approx(31.2, abs=0.5)


def test_grid_search_matches_brute_force_far_from_other_outlets():
    outlets = geo.load_outlets(FIXTURES / "library_outlets.csv")
    grid = geo.LibraryGrid(outlets)
    # A point in the Pacific is far from every outlet. The search must still find the 3 nearest.
    lat, lon = 21.3, -157.8
    brute = sorted(geo.haversine_miles(lat, lon, o["lat"], o["lon"]) for o in outlets)[:3]
    assert [m for m, _ in grid.nearest(lat, lon)] == pytest.approx(brute)


def test_fictitious_district_rows_merge_into_the_real_district():
    # Real GRF rows for ZCTA 77964. NCES lists Hallettsville ISD (9-12) as a separate row that
    # covers the same ground as Vysehrad ISD. The row becomes Hallettsville ISD itself.
    grf = [("77964", "4822120", 427.1473), ("77964", "4848285", 23.6756), ("77964", "4844250", 23.6756)]
    nces = {r["leaid"]: r for r in districts.read_nces_csv(FIXTURES / "nces_districts.csv")}
    parent_of = {k: r["parent_leaid"] for k, r in nces.items() if r["parent_leaid"]}
    rows = geo.merge_zcta_districts(grf, parent_of, nces)
    assert [(r[1], r[2]) for r in rows] == [("4822120", "450.8229"), ("4844250", "23.6756")]


def test_smart_title_fixes_outlet_names():
    assert geo.smart_title("SALT LAKE CITY PUBLIC LIBRARY") == "Salt Lake City Public Library"
    assert geo.smart_title("2100 NW 1ST AVE") == "2100 NW 1st Ave"
    assert geo.smart_title("CHILDREN'S LIBRARY OF THE VALLEY") == "Children's Library of the Valley"
    assert geo.smart_title("JOHN MCCULLOUGH FREE") == "John McCullough Free"

"""Tests for pipeline/districts.py.

The district names below are real names of US school districts. Each one must match a row in
tests/fixtures/geo/nces_districts.csv. Those rows are real rows from the public NCES 2024-25
district file. The tests spell some names the way a source might spell them, such as a typo or a
short form. The tests do not read any file in sources/.
"""
import csv
from pathlib import Path

import pytest

from pipeline import districts

FIXTURES = Path(__file__).parent / "fixtures" / "geo"
NCES = FIXTURES / "nces_districts.csv"


def match(pairs, tmp_path, counts=None):
    return districts.match_districts(pairs, nces_csv=NCES, review_path=tmp_path / "review.csv", counts=counts)


def test_exact_match_after_normalizing_the_district_type(tmp_path):
    # A source can write "Davis School District". NCES writes the same words, so this is an exact match.
    result = match([("UT", "Davis School District")], tmp_path)
    assert result[("UT", "Davis School District")] == {
        "leaid": "4900210", "nces_name": "Davis School District", "method": "exact", "score": 100}


def test_isd_and_independent_school_district_are_the_same(tmp_path):
    assert districts.normalize_name("Katy ISD") == districts.normalize_name("Katy Independent School District")
    result = match([("TX", "Katy ISD"), ("TX", "North East ISD")], tmp_path)
    assert result[("TX", "Katy ISD")]["leaid"] == "4825170"
    assert result[("TX", "North East ISD")]["leaid"] == "4832940"


def test_usd_with_a_district_number_matches_a_plain_name(tmp_path):
    # Magnusson writes "Derby Public Schools". NCES writes "Derby Unified School District 260".
    result = match([("KS", "Derby Public Schools"), ("KS", "Derby USD 260")], tmp_path)
    assert result[("KS", "Derby Public Schools")]["leaid"] == "2005460"
    assert result[("KS", "Derby USD 260")]["leaid"] == "2005460"


def test_florida_forms(tmp_path):
    pairs = [("FL", "School District of Manatee County"), ("FL", "Clay County District Schools"),
             ("FL", "Orange County Public Schools")]
    result = match(pairs, tmp_path)
    assert result[pairs[0]]["leaid"] == "1201230"       # Manatee County School District
    assert result[pairs[1]]["leaid"] == "1200300"       # Clay County School District
    assert result[pairs[2]]["leaid"] == "1201440"       # Orange County School District


def test_a_bare_name_matches_by_core_name(tmp_path):
    result = match([("TX", "Eanes")], tmp_path)
    assert result[("TX", "Eanes")]["method"] == "core"
    assert result[("TX", "Eanes")]["leaid"] == "4817760"


def test_fuzzy_match_accepts_a_typo_that_is_far_ahead(tmp_path):
    # PEN spells Carroll with one r. The score is 97 and no other Maryland district is close.
    result = match([("MD", "Caroll County School District")], tmp_path)
    assert result[("MD", "Caroll County School District")]["leaid"] == "2400210"
    assert result[("MD", "Caroll County School District")]["method"] == "fuzzy"


def test_fuzzy_match_rejects_a_score_below_the_threshold(tmp_path):
    # "Southwest Valley" scores 92.3 against the real "Southeast Valley". That is a different
    # district, so the matcher must return no match.
    pair = ("IA", "Southwest Valley School District")
    result = match([pair], tmp_path)
    assert result[pair]["leaid"] == ""
    assert result[pair]["method"] == "none"


def test_two_districts_with_one_name_are_not_guessed(tmp_path):
    # Texas has two real "Highland Park Independent School District" rows (Dallas and Amarillo).
    pair = ("TX", "Highland Park ISD")
    assert match([pair], tmp_path)[pair]["leaid"] == ""


def test_different_district_numbers_are_not_merged(tmp_path):
    # "Derby USD 261" does not exist. It must not match "Derby Unified School District 260".
    pair = ("KS", "Derby USD 261")
    assert match([pair], tmp_path)[pair]["leaid"] == ""


def test_a_name_in_the_wrong_state_does_not_match(tmp_path):
    pair = ("OK", "Katy ISD")
    assert match([pair], tmp_path)[pair]["leaid"] == ""


def test_unmatched_pairs_go_to_the_review_file_with_counts(tmp_path, capsys):
    pairs = [("TX", "Katy ISD"), ("", "Cedar Network Overseas Schools"),
             ("IA", "Southwest Valley School District")]
    counts = {pairs[0]: 10, pairs[1]: 607, pairs[2]: 3}
    match(pairs, tmp_path, counts)
    with open(tmp_path / "review.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["name"] for r in rows] == ["Cedar Network Overseas Schools", "Southwest Valley School District"]
    assert rows[0]["ban_records"] == "607"
    assert rows[1]["best_candidate"] == "Southeast Valley Community School District"
    assert float(rows[1]["score"]) < districts.FUZZY_MIN_SCORE
    # 1 of 3 unique names matched. 10 of 620 ban records matched.
    assert "1/3 unique names" in capsys.readouterr().out


def test_fictitious_nces_row_links_to_its_real_district():
    rows = [
        {"STATEFP": "48", "GEOID": "4822120", "NAME": "Hallettsville Independent School District",
         "LOGRADE": "PK", "HIGRADE": "12", "FUNCSTAT": "E"},
        {"STATEFP": "48", "GEOID": "4848285",
         "NAME": "Hallettsville Independent School District (9-12) in Vysehrad",
         "LOGRADE": "09", "HIGRADE": "12", "FUNCSTAT": "F"},
    ]
    built = {r["leaid"]: r for r in districts.build_nces_rows(rows)}
    assert built["4848285"]["parent_leaid"] == "4822120"
    assert built["4822120"]["parent_leaid"] == ""
    assert built["4822120"]["state"] == "TX"


def test_read_dbf_reads_a_small_file(tmp_path):
    import struct
    # A dBase III file with one field "NAME" (5 characters) and two records.
    header = bytearray(32)
    header[0] = 3
    struct.pack_into("<IHH", header, 4, 2, 32 + 32 + 1, 1 + 5)
    field = bytearray(32)
    field[0:4] = b"NAME"
    field[11] = ord("C")
    field[16] = 5
    data = bytes(header) + bytes(field) + b"\r" + b" Katy " + b" Eanes"
    path = tmp_path / "t.dbf"
    path.write_bytes(data)
    assert districts.read_dbf(path) == [{"NAME": "Katy"}, {"NAME": "Eanes"}]


def test_core_match_ignores_spaces_inside_names():
    # Real names: PEN writes "Adel DeSoto Minburn", NCES writes "Adel-De Soto-Minburn".
    from pipeline.districts import match_districts
    rows = [{"leaid": "1903150", "state": "IA", "name": "Adel-De Soto-Minburn Community School District",
             "funcstat": "E"}]
    import csv, tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nces.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        result = match_districts([("IA", "Adel DeSoto Minburn Community School District")],
                                 nces_csv=path, review_path=os.path.join(tmp, "review.csv"))
    assert result[("IA", "Adel DeSoto Minburn Community School District")]["leaid"] == "1903150"

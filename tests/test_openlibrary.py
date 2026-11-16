"""Tests for pipeline/openlibrary.py.

Part 1 tests the scoring with hand-made candidates.
Part 2 runs extract() end to end on small gzip dumps in tests/fixtures/openlibrary/.
The works and authors lines in the fixtures are real lines from the Open Library
monthly dump. See tests/fixtures/openlibrary/README.txt for how they were made.
"""
import csv
import shutil
from pathlib import Path

import pytest

from pipeline import openlibrary as ol

FIXTURES = Path(__file__).parent / "fixtures" / "openlibrary"


# ------------------------------------------------------------ scoring tests

def need(title, author_surnames):
    return {"title": title, "main": title, "surnames": set(author_surnames)}


def cand(work, title, editions=0, cover="", author_ids=("A1",), subtitle=""):
    c = ol.new_candidate(work, title, subtitle, list(author_ids), cover)
    c["editions"] = editions
    return c


NAMES = {"A1": "Maia Kobabe", "A2": "Someone Else"}


def test_exact_title_and_author_is_high():
    best = ol.best_match(need("gender queer", ["kobabe"]), [cand("OL1W", "Gender Queer")], NAMES)
    assert best["confidence"] == "high"
    assert best["title_score"] == 100
    assert best["author_match"] == 1


def test_right_title_wrong_author_is_low():
    best = ol.best_match(need("gender queer", ["kobabe"]),
                         [cand("OL1W", "Gender Queer", author_ids=["A2"])], NAMES)
    assert best["confidence"] == "low"
    assert best["author_match"] == 0


def test_author_surname_must_be_a_whole_word():
    assert ol.author_matches({"obab"}, ["Maia Kobabe"]) == 0
    assert ol.author_matches({"kobabe"}, ["Maia Kobabe"]) == 1


def test_author_match_ignores_accents():
    assert ol.author_matches({"aciman"}, ["André Aciman"]) == 1
    assert ol.author_matches({"marquez"}, ["Gabriel García Márquez"]) == 1


def test_author_match_looks_at_every_work_author():
    assert ol.author_matches({"kobabe"}, ["Someone Else", "Maia Kobabe"]) == 1


def test_author_match_multi_word_surname_and_suffix():
    assert ol.author_matches({"cavell clarke"}, ["Steffi Cavell-Clarke"]) == 1
    assert ol.author_matches({"vonnegut jr"}, ["Kurt Vonnegut"]) == 1
    assert ol.author_matches({"clarke"}, ["Steffi Cavell-Clarke"]) == 1
    assert ol.author_matches(set(), ["Kurt Vonnegut"]) == 0


def test_author_match_allows_one_typo_in_a_long_surname():
    assert ol.author_matches({"guvatt"}, ["Nicholas Guyatt"]) == 1
    assert ol.author_matches({"rodriquez"}, ["Luis J. Rodriguez"]) == 1
    assert ol.author_matches({"lee"}, ["Lea Smith"]) == 0     # short names stay exact
    assert ol.author_matches({"hurston"}, ["Jacob Agwa"]) == 0


def test_work_with_no_authors_is_not_an_author_match():
    best = ol.best_match(need("gender queer", ["kobabe"]),
                         [cand("OL1W", "Gender Queer", author_ids=[])], NAMES)
    assert best["confidence"] == "low"


def test_subtitle_form_matches_source_title_with_subtitle():
    # The source says "Gender Queer A Memoir". OL splits it into title and subtitle.
    c = cand("OL1W", "Gender Queer", subtitle="A Memoir")
    best = ol.best_match(need("gender queer a memoir", ["kobabe"]), [c], NAMES)
    assert best["title_score"] == 100
    assert best["confidence"] == "high"


def test_source_main_title_matches_work_without_subtitle():
    n = {"title": "mediocre the dangerous legacy of white male america", "main": "mediocre",
         "surnames": {"oluo"}}
    best = ol.best_match(n, [cand("OL1W", "Mediocre", author_ids=["A3"])],
                         {"A3": "Ijeoma Oluo"})
    assert best["title_score"] == 100 and best["confidence"] == "high"


def test_source_main_title_is_not_compared_with_work_main_title():
    n = {"title": "dune messiah", "main": "dune", "surnames": {"herbert"}}
    best = ol.best_match(n, [cand("OL1W", "Dune: Children of Dune", author_ids=["A3"])],
                         {"A3": "Frank Herbert"})
    assert best["confidence"] == "low"


def test_main_title_forms_cover_colon_dash_and_paren():
    assert "dune" in ol.title_forms("Dune: Messiah")
    assert "dune" in ol.title_forms("Dune - Messiah")
    assert "dune" in ol.title_forms("Dune (Book 1)")
    assert ol.main_title("Plain Title") == "Plain Title"


def test_threshold_is_90():
    # "gender queer" vs "gender queers": ratio = 2*12/(12+13) = 96 -> high.
    near = ol.best_match(need("gender queer", ["kobabe"]), [cand("OL1W", "Gender Queers")], NAMES)
    assert near["title_score"] == 96 and near["confidence"] == "high"
    # A short title with one wrong letter falls under 90.
    far = ol.best_match(need("the hater", ["kobabe"]), [cand("OL1W", "Haters")], NAMES)
    assert far["title_score"] < 90 and far["confidence"] == "low"


def test_preference_high_beats_better_scoring_low():
    low = cand("OL1W", "Gender Queer", editions=99, author_ids=["A2"])
    high = cand("OL2W", "Gender Queers", editions=1, author_ids=["A1"])
    best = ol.best_match(need("gender queer", ["kobabe"]), [low, high], NAMES)
    assert best["work"] == "OL2W"


def test_preference_order_after_confidence():
    n = need("gender queer", ["kobabe"])
    # Same confidence: higher title score first.
    a = cand("OL5W", "Gender Queers")      # 96
    b = cand("OL9W", "Gender Queer")       # 100
    assert ol.best_match(n, [a, b], NAMES)["work"] == "OL9W"
    # Same score: more editions first.
    a = cand("OL5W", "Gender Queer", editions=2)
    b = cand("OL9W", "Gender Queer", editions=7)
    assert ol.best_match(n, [a, b], NAMES)["work"] == "OL9W"
    # Same editions: has a cover first.
    a = cand("OL5W", "Gender Queer", editions=2)
    b = cand("OL9W", "Gender Queer", editions=2, cover="77")
    assert ol.best_match(n, [a, b], NAMES)["work"] == "OL9W"
    # Same cover state: lower OL number first (and 99 < 100 as numbers, not as text).
    a = cand("OL100W", "Gender Queer")
    b = cand("OL99W", "Gender Queer")
    assert ol.best_match(n, [a, b], NAMES)["work"] == "OL99W"


def test_no_candidate_gives_none_row():
    assert ol.best_match(need("x", ["y"]), [], NAMES) is None
    assert ol.to_row("x|y", None) == {"key": "x|y", "confidence": "none"}


def test_add_edition_collects_totals():
    c = cand("OL1W", "Gender Queer")
    ol.add_edition(c, {"isbn_10": ["0000000001"]})
    ol.add_edition(c, {"isbn_13": ["9781111111111"], "covers": [5], "ocaid": "genderqueer00koba"})
    ol.add_edition(c, {"isbn_13": ["9782222222222"]})
    assert c["editions"] == 3
    assert c["isbn"] == "9781111111111"   # the edition with a cover wins
    assert c["cover_id"] == "5"           # the work had none
    assert c["ia_id"] == "genderqueer00koba"


def test_add_edition_keeps_work_cover_and_ignores_negative_cover():
    c = cand("OL1W", "Gender Queer", cover="42")
    ol.add_edition(c, {"covers": [-1, 7]})
    assert c["cover_id"] == "42"
    c2 = cand("OL2W", "Gender Queer")
    ol.add_edition(c2, {"covers": [-1]})
    assert c2["cover_id"] == ""


def test_collect_needs_merges_pairs_with_same_key():
    needs = ol.collect_needs([("The Haters", "Andrews, Jesse"), ("Haters", "Jesse Andrews")])
    assert list(needs) == ["haters|andrews"]
    assert needs["haters|andrews"]["surnames"] == {"andrews"}


# ------------------------------------------------------- end-to-end tests

PAIRS = [
    ("Call Me By Your Name", "Aciman, André"),
    ("Gender Queer", "Kobabe, Maia"),
    ("The Perks of Being a Wallflower", "Chbosky, Stephen"),
    ("The Haters", "Andrews, Jesse"),
    ("Call Me By Your Name", "Guadagnino, Luca"),   # two works tie
    ("Gender Queer", "Wrong, Author"),       # right title, wrong author -> low
    ("Book That Does Not Exist Anywhere", "Nobody, Nemo"),  # -> none
]


def test_extract_end_to_end(tmp_path, capsys):
    cache = tmp_path / "ol_matches.csv"
    summary = ol.extract(PAIRS, FIXTURES, cache)
    rows = ol.load_matches(cache)
    assert summary == {"requested": 7, "cached": 0, "high": 5, "low": 1, "none": 1}
    assert list(rows) == sorted(rows)
    assert "7 keys requested, 0 already cached" in capsys.readouterr().out

    call = rows["call me by your name|aciman"]
    assert call["confidence"] == "high"
    assert "Aciman" in call["ol_authors"]
    assert rows["gender queer|wrong"]["confidence"] == "low"
    assert rows["book that does not exist anywhere|nobody"]["confidence"] == "none"
    assert rows["book that does not exist anywhere|nobody"]["work"] == ""


def test_extract_collects_isbn_cover_and_scan_id(tmp_path):
    cache = tmp_path / "ol_matches.csv"
    ol.extract(PAIRS, FIXTURES, cache)
    row = ol.load_matches(cache)["call me by your name|aciman"]
    assert row["work"] == "OL8034526W"
    assert row["ol_authors"] == "André Aciman"
    assert row["isbn"] == "9788551002735"        # real edition data from the dump
    assert row["cover_id"] == "11123807"         # the work's own cover comes first
    assert row["ia_id"] == "callmebyyourname00acim"
    assert row["title_score"] == "100" and row["author_match"] == "1"


def test_extract_gives_no_isbn_when_the_work_has_no_edition_data(tmp_path):
    # OL19737146W has one edition in the fixture, with no ISBN, cover, or scan.
    cache = tmp_path / "ol_matches.csv"
    ol.extract(PAIRS, FIXTURES, cache)
    row = ol.load_matches(cache)["call me by your name|guadagnino"]
    assert row["work"] == "OL19737146W"       # it ties with OL19737185W; the lower OL number wins
    assert (row["isbn"], row["cover_id"], row["ia_id"]) == ("", "", "")


def test_cache_file_columns_and_order(tmp_path):
    cache = tmp_path / "ol_matches.csv"
    ol.extract(PAIRS, FIXTURES, cache)
    with open(cache, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["key", "work", "ol_title", "ol_authors", "title_score",
                                     "author_match", "confidence", "isbn", "cover_id", "ia_id"]
        keys = [r["key"] for r in reader]
    assert keys == sorted(keys)


def test_second_run_does_not_touch_the_dumps(tmp_path, capsys):
    cache = tmp_path / "ol_matches.csv"
    ol.extract(PAIRS, FIXTURES, cache)
    capsys.readouterr()
    empty_dir = tmp_path / "no_dumps"          # a missing dump would raise an error
    summary = ol.extract(PAIRS, empty_dir, cache)
    assert summary["cached"] == 7 and summary["high"] == 0
    assert "7 already cached" in capsys.readouterr().out


def test_only_missing_keys_are_added(tmp_path):
    cache = tmp_path / "ol_matches.csv"
    ol.extract(PAIRS[:2], FIXTURES, cache)
    before = ol.load_matches(cache)
    summary = ol.extract(PAIRS, FIXTURES, cache)
    after = ol.load_matches(cache)
    assert summary["cached"] == 2 and summary["requested"] == 7
    assert len(after) == 7
    for key, row in before.items():
        assert after[key] == row


def test_incomplete_dump_raises_unless_allowed(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copy(FIXTURES / "ol_dump_works_latest.txt.gz", raw)
    data = (FIXTURES / "ol_dump_authors_latest.txt.gz").read_bytes()
    (raw / "ol_dump_authors_latest.txt.gz").write_bytes(data[: len(data) // 2])
    with pytest.raises(ol.IncompleteDump):
        ol.extract(PAIRS, raw, tmp_path / "a.csv")
    ol.extract(PAIRS, raw, tmp_path / "b.csv", allow_partial=True)


def test_load_matches_missing_file_is_empty(tmp_path):
    assert ol.load_matches(tmp_path / "nope.csv") == {}

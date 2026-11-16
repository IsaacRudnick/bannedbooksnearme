"""Tests for the 2024-25 additions: PEN "Nation" rows and the weeded status, the PEN state mandated
loader, and the Utah State Board of Education loader. They use made-up rows in the real column layouts.
No test needs a file in sources/. The last test checks the whole real files and skips when a file is missing.
"""
from collections import Counter
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from pipeline.loaders import RECORD_KEYS, SourceFormatError, pen, pen_state, utah

FIXTURES = Path(__file__).parent / "fixtures" / "loaders"
REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- PEN 2024-25 (Nation rows and weeded status)

def load_pen_2425_sample(stats=None):
    # Made-up rows in the layout of sources/pen_2024-25_index.csv: 5 school rows and 3 "Nation" rows.
    return pen.load(FIXTURES / "pen_2024-25_sample.csv", "pen_2024-25", school_year="2024-25", stats=stats)


def test_pen_professionally_weeded_counts_as_banned():
    assert pen.map_status("Banned - Professionally Weeded") == "banned"
    records = load_pen_2425_sample()
    weeded = [r for r in records if r["title"] == "2031: A Graphic Novel"]
    assert len(weeded) == 1 and weeded[0]["status"] == "banned"
    assert weeded[0]["state"] == "FL" and weeded[0]["district_name"] == "Saltmarsh County Schools"


def test_pen_nation_rows_are_dropped_and_counted():
    stats = Counter()
    records = load_pen_2425_sample(stats)
    assert len(records) == 5
    assert stats["dropped_state:Nation"] == 3
    assert "Overseas Schools Educational Activitity" not in {r["district_name"] for r in records}


def test_pen_other_unknown_state_still_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "Title,State,District,Ban Status,Date of Challenge/Removal,Series,Author,Illustrator(s),Translator(s)\n"
        "X,Atlantis,Some District,Banned,June 2025,,\"Doe, Jane\",,\n", encoding="utf-8")
    with pytest.raises(SourceFormatError, match="unknown state"):
        pen.load(bad, "pen_2024-25", school_year="2024-25")


# ---------------------------------------------------------------- PEN state mandated

def load_pen_state_sample():
    # Made-up rows in the layout of sources/pen_2024-25_state_mandated.csv.
    return pen_state.load(FIXTURES / "pen_state_sample.csv", "pen_2024-25_state")


def test_pen_state_records_are_statewide_banned_with_no_author():
    records = load_pen_state_sample()
    assert len(records) == 6
    assert all(tuple(r) == RECORD_KEYS for r in records)
    assert {r["scope"] for r in records} == {"statewide"}
    assert {r["status"] for r in records} == {"banned"}
    assert {r["author"] for r in records} == {""}
    assert {r["district_name"] for r in records} == {""}
    assert [r["state"] for r in records] == ["SC", "SC", "UT", "UT", "UT", "UT"]


def test_pen_state_school_year_follows_the_month_rule():
    by_title = {(r["state"], r["title"]): r for r in load_pen_state_sample()}
    may_2025 = by_title[("SC", "Trinkets")]
    assert may_2025["school_year"] == "2024-25" and may_2025["calendar_year"] == "2025"
    march_2024 = by_title[("UT", "Trinkets")]
    assert march_2024["school_year"] == "2023-24" and march_2024["calendar_year"] == "2024"
    august_2024 = by_title[("UT", "Kingdom of Embers")]
    assert august_2024["school_year"] == "2024-25"


def test_pen_state_removes_initials_and_zero_width_characters():
    titles = [r["title"] for r in load_pen_state_sample()]
    assert "Fortune" in titles and "Slant" in titles       # the file has "Fortune (QR)" and "Slant (MN)"
    assert "A Crown of Salt Marsh" in titles               # the file has a zero-width space after "A "
    assert not any("​" in t for t in titles)


def test_pen_state_header_check_names_missing_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("State,Title\nUtah,Trinkets\n", encoding="utf-8")
    with pytest.raises(SourceFormatError) as error:
        pen_state.load(bad, "pen_2024-25_state")
    assert "District" in str(error.value) and "Date of Challenge/Removal" in str(error.value)


def test_pen_state_bad_date_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("State,District,Title,Date of Challenge/Removal\nUtah,Utah State Board of Education,Trinkets,May 2025\n",
                   encoding="utf-8")
    with pytest.raises(SourceFormatError, match="ISO date"):
        pen_state.load(bad, "pen_2024-25_state")


# ---------------------------------------------------------------- Utah State Board of Education (XLSX)

# Made-up rows in the layout of sources/utah_statewide_removals.xlsx, as openpyxl reads them.
# The real sheet has note rows above the header, and date cells that openpyxl reads as datetime.
UTAH_NOTE_ROWS = [
    ("#VALUE!", None, None, None, None, None, None),
    ("In accordance with Example Code 12-3-456, the titles below have been determined to contain Sample "
     "Materials in a final determination AND have met the statewide threshold of removal.",
     None, None, None, None, None, None),
    ("FAQ", None, None, None, None, None, None),
]
UTAH_HEADER = ("Title", "Author", "Removed in Districts", "Removed in Charters", "Located in Grades",
               "Original Publication Date", "Date threshold met")
UTAH_ROWS = [
    ("A Crown Of Salt Marsh (Marsh Crown Saga #2)", "Brandt, Elise M.", "Alder, Birch, Cedar, Dune",
     None, "9-12", 2016, datetime(2024, 8, 2)),
    ("Trinkets (Trinkets, Book 1)", "Hartwell, Eleanor", "Birch, Fir, Dune", None, "7-12", 2009, datetime(2025, 3, 6)),
    ("Barn Swallows in Winter", "Lowell, Greta ", "Elm, Birch, Fir", None, "7-12", 2006, datetime(2025, 5, 5)),
    ("Ember", "Zephyr", "Birch, Cedar,  Dune", None, "7-12", 1996, datetime(2026, 8, 19)),
    ("Hollow: The Life and Times of the Hollow King", "Mallory, Gregor", "Birch, Fir, Dune",
     None, "7-12", 1995, datetime(2026, 1, 5)),
]


def make_utah_workbook(path, rows=UTAH_ROWS, note_rows=UTAH_NOTE_ROWS, sheet="Titles", header=UTAH_HEADER):
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for row in [*note_rows, header, *rows]:
        worksheet.append(list(row))
    workbook.save(path)
    return path


@pytest.fixture
def utah_records(tmp_path):
    return utah.load(make_utah_workbook(tmp_path / "utah.xlsx"), "utah_usbe")


def test_utah_finds_the_header_below_the_note_rows(utah_records):
    assert len(utah_records) == 5
    assert all(tuple(r) == RECORD_KEYS for r in utah_records)
    assert utah_records[0]["source_row"] == "5"        # the first data row is spreadsheet row 5
    assert {r["scope"] for r in utah_records} == {"statewide"}
    assert {r["status"] for r in utah_records} == {"banned"}
    assert {r["state"] for r in utah_records} == {"UT"} and {r["district_name"] for r in utah_records} == {""}


def test_utah_header_row_does_not_have_to_be_row_4(tmp_path):
    path = make_utah_workbook(tmp_path / "utah.xlsx", note_rows=UTAH_NOTE_ROWS[:1])
    records = utah.load(path, "utah_usbe")
    assert len(records) == 5 and records[0]["source_row"] == "3"


def test_utah_removes_only_a_trailing_series_note_from_the_title(utah_records):
    titles = [r["title"] for r in utah_records]
    assert titles == ["A Crown Of Salt Marsh", "Trinkets", "Barn Swallows in Winter", "Ember",
                      "Hollow: The Life and Times of the Hollow King"]


def test_utah_strips_author_whitespace(utah_records):
    assert utah_records[2]["author"] == "Lowell, Greta"


def test_utah_school_year_follows_the_month_rule(utah_records):
    years = [(r["school_year"], r["calendar_year"]) for r in utah_records]
    assert years == [("2024-25", "2024"), ("2024-25", "2025"), ("2024-25", "2025"), ("2026-27", "2026"),
                     ("2025-26", "2026")]


def test_utah_note_lists_districts_and_grades_and_collapses_spaces(utah_records):
    assert utah_records[0]["note"] == ("Removed by these Utah districts before the statewide rule applied: "
                                       "Alder, Birch, Cedar, Dune. Grades 9-12.")
    assert utah_records[3]["note"] == ("Removed by these Utah districts before the statewide rule applied: "
                                       "Birch, Cedar, Dune. Grades 7-12.")   # the cell has a double space


def test_utah_note_names_charters_only_when_the_cell_has_text(tmp_path):
    row = ("Trinkets (Trinkets, Book 1)", "Hartwell, Eleanor", "Birch, Fir", "Juniper Charter", "7-12", 2009,
           datetime(2025, 3, 6))
    (record,) = utah.load(make_utah_workbook(tmp_path / "utah.xlsx", rows=[row]), "utah_usbe")
    assert "Removed by these Utah charter schools: Juniper Charter." in record["note"]
    (plain,) = utah.load(make_utah_workbook(tmp_path / "plain.xlsx", rows=UTAH_ROWS[1:2]), "utah_usbe")
    assert "charter" not in plain["note"]


def test_utah_missing_column_names_the_column(tmp_path):
    header = tuple(name for name in UTAH_HEADER if name != "Located in Grades")
    rows = [row[:4] + row[5:] for row in UTAH_ROWS]
    with pytest.raises(SourceFormatError, match="Located in Grades"):
        utah.load(make_utah_workbook(tmp_path / "utah.xlsx", rows=rows, header=header), "utah_usbe")


def test_utah_no_header_row_raises(tmp_path):
    with pytest.raises(SourceFormatError, match="no header row"):
        utah.load(make_utah_workbook(tmp_path / "utah.xlsx", rows=[], header=("Name", "Who")), "utah_usbe")


def test_utah_wrong_sheet_name_raises(tmp_path):
    with pytest.raises(SourceFormatError, match="Titles"):
        utah.load(make_utah_workbook(tmp_path / "utah.xlsx", sheet="Other"), "utah_usbe")


# ---------------------------------------------------------------- registry and whole files

def test_registry_lists_the_four_new_sources():
    from pipeline.sources import by_id
    sources = by_id()
    expected = {"pen_2024-25": 1, "pen_2024-25_state": 2, "utah_usbe": 1, "fldoe_2025-26": 2}
    assert {sid: sources[sid].priority for sid in expected} == expected
    assert sources["pen_2024-25"].args == {"school_year": "2024-25"}
    assert sources["fldoe_2025-26"].args["school_year"] == "2025-26"


# Extra checks on the whole real files. Each one skips when its file is not in sources/.
def real_file_case(source_id, expected):
    from pipeline.sources import by_id
    path = REPO / by_id()[source_id].path
    return pytest.param(source_id, expected, id=source_id, marks=pytest.mark.skipif(
        not path.exists(),
        reason=f"{path.relative_to(REPO).as_posix()} is not in this checkout. See the README section 'Get the source files'."))


@pytest.mark.parametrize("source_id, expected", [
    real_file_case("pen_2024-25", 6129),        # 6,719 rows minus 590 "Nation" rows
    real_file_case("pen_2024-25_state", 40),
    real_file_case("utah_usbe", 44),
    real_file_case("fldoe_2025-26", 620),
])
def test_real_files_give_the_expected_record_counts(source_id, expected):
    from pipeline.sources import by_id
    source = by_id()[source_id]
    stats = Counter()
    assert len(source.loader(REPO / source.path, source_id, stats=stats, **source.args)) == expected
    if source_id == "pen_2024-25":
        assert stats["dropped_state:Nation"] == 590

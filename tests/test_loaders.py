"""Tests for the three source loaders.

The fixtures in tests/fixtures/loaders/ hold made-up rows in the real column layouts. The tests
need no file in sources/. The test of the whole real Florida PDF skips when the file is missing.
"""
from collections import Counter
from pathlib import Path

import pytest

from pipeline.loaders import RECORD_KEYS, SourceFormatError, fldoe, magnusson, pen

FIXTURES = Path(__file__).parent / "fixtures" / "loaders"
REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- PEN

def load_pen_sample():
    return pen.load(FIXTURES / "pen_sample.csv", "pen_2023-24", school_year="2023-24")


def test_pen_record_has_exactly_the_contract_keys():
    records = load_pen_sample()
    assert len(records) == 5
    assert all(tuple(r) == RECORD_KEYS for r in records)
    assert all(isinstance(v, str) for r in records for v in r.values())


def test_pen_status_map():
    statuses = [r["status"] for r in load_pen_sample()]
    assert statuses == ["banned", "banned_pending_investigation", "restricted", "banned", "banned"]


def test_pen_unknown_status_raises():
    with pytest.raises(SourceFormatError, match="Unknown PEN Ban Status"):
        pen.map_status("Something new")


def test_pen_district_is_the_district_column():
    first = load_pen_sample()[0]
    assert first["district_name"] == "Pine Hollow Borough School District"
    assert first["state"] == "AK"
    assert first["scope"] == "district"


def test_pen_school_year_comes_from_registry_and_calendar_year_from_date():
    records = load_pen_sample()
    assert {r["school_year"] for r in records} == {"2023-24"}
    assert records[0]["calendar_year"] == "2023"      # "September 2023"
    assert records[2]["calendar_year"] == "2024"      # a 2024 date
    assert pen.year_from_date("Fall 2023") == "2023"
    assert pen.year_from_date("") == ""


def test_pen_header_check_names_missing_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Title,Author,State\nX,Y,Texas\n", encoding="utf-8")
    with pytest.raises(SourceFormatError) as error:
        pen.load(bad, "pen_x", school_year="2024-25")
    assert "District" in str(error.value) and "Ban Status" in str(error.value)


# ---------------------------------------------------------------- Magnusson

def load_magnusson_sample():
    stats = Counter()
    records = magnusson.load(FIXTURES / "magnusson_sample.csv", "magnusson", stats=stats)
    return records, stats


def test_magnusson_keeps_only_school_ban_rows():
    records, stats = load_magnusson_sample()
    assert [r["source_row"] for r in records] == ["9001", "9002", "9003", "9004", "9005", "9006", "9012"]
    assert stats["dropped_library_type:Public"] == 2      # a public library ban row, and a public Retained row
    assert stats["dropped_unknown_state:US"] == 1         # overseas schools have no state
    assert stats["retained_with_restrictions_yes (not included)"] == 1


def test_magnusson_district_is_overseeing_agency():
    records, _ = load_magnusson_sample()
    by_row = {r["source_row"]: r for r in records}
    assert by_row["9002"]["district_name"] == "Marlow County Public Schools"
    assert by_row["9001"]["district_name"] == "Pine Hollow ISD"


def test_magnusson_status_map():
    records, _ = load_magnusson_sample()
    by_row = {r["source_row"]: r["status"] for r in records}
    assert by_row["9001"] == "banned"
    assert by_row["9004"] == "restricted"                      # Retained/Restricted
    assert by_row["9006"] == "banned_pending_investigation"   # Still in Process + Off Shelves


@pytest.mark.parametrize("decision, response, expected", [
    ("Banned/Removed", "", "banned"),
    ("  banned/removedbanned/removed ", "", "banned"),
    ("Removed - Curriculum", "Unknown", "banned"),
    ("Retained Restricted", "", "restricted"),
    ("Challenged - Removed While Under Review", "", "banned_pending_investigation"),
    ("Still in Process", "Off Shelves", "banned_pending_investigation"),
    ("Still in ProcessStill in Process", "off shelves", "banned_pending_investigation"),
    ("Still in Process", "Unknown", ""),
    ("Still in Process", "On shelves", ""),
    ("Retained", "Off Shelves", ""),
    ("Reshelved", "", ""),
    ("Weeded/Deselectednned", "", ""),
    ("", "", ""),
])
def test_magnusson_map_decision(decision, response, expected):
    assert magnusson.map_decision(decision, response) == expected


def test_magnusson_source_url_is_first_web_link():
    records, _ = load_magnusson_sample()
    by_row = {r["source_row"]: r for r in records}
    assert by_row["9002"]["source_url"].startswith("https://news.example.com/")
    # Links1 of this row says "PDF Files". That is a note, so the loader takes the next real link.
    assert by_row["9006"]["source_url"].startswith("https://harlanreview.example.com/")
    row = {"Links1": "", "Links2": "PDF Files", "Links3": "http://x.org", "Links4": ""}
    assert magnusson.first_link(row) == "http://x.org"


@pytest.mark.parametrize("month, year, expected", [
    ("09", "2023", ("2023-24", "2023")),     # July to December starts the school year
    ("6", "2023", ("2022-23", "2023")),      # January to June ends it
    ("January", "2024", ("2023-24", "2024")),
    ("July", "2023", ("2023-24", "2023")),
    ("Fall", "2021", ("2021-22", "2021")),
    ("Spring", "2022", ("2021-22", "2022")),
    ("Summer", "2024", ("", "2024")),
    ("Unclear", "2022", ("", "2022")),
    ("", "2022", ("", "2022")),
    ("SY", "2223", ("2022-23", "")),         # two-year code
    ("22-23", "", ("2022-23", "")),
    ("SY22-23", "2023", ("2022-23", "")),
    ("12", "2021", ("2021-22", "2021")),     # 2021 is a real year, not a code
    ("12", "", ("", "")),
    ("SY", "2425", ("2024-25", "")),
])
def test_magnusson_school_year_for(month, year, expected):
    assert magnusson.school_year_for(month, year) == expected


def test_magnusson_sample_school_years():
    records, _ = load_magnusson_sample()
    by_row = {r["source_row"]: (r["school_year"], r["calendar_year"]) for r in records}
    assert by_row["9002"] == ("2023-24", "2023")    # month 09, year 2023
    assert by_row["9003"] == ("2023-24", "2024")    # month 01, year 2024
    assert by_row["9005"] == ("2022-23", "")         # year 2223


def test_magnusson_header_check_names_missing_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("ID,New_Book_Title\n1,X\n", encoding="utf-8")
    with pytest.raises(SourceFormatError) as error:
        magnusson.load(bad, "magnusson")
    assert "Overseeing_Agency" in str(error.value) and "Decision" in str(error.value)


# ---------------------------------------------------------------- Florida DOE PDF

PDF = REPO / "sources" / "fldoe_2024-25_SDRPS.pdf"
PDF_URL = "https://cdn.fldoe.org/file/5574/2425-SDRPS-100628-2.pdf"

# Made-up table rows in the layout of the PDF: one [cell, cell] row per table row, in page order.
FL_ROWS = [
    ["2024-2025 School District Reporting", None],
    ["Title", "Author"],
    ["Alder\nNone Removed or Discontinued", None],
    ["Birch", None],
    ["Grades K-5", None],
    ["The Lantern Keeper", "Dana Okafor"],
    ["Grades 9-12", None],
    ["Hey, Neighbor: How I Found My Voice\nSecond Line", "Wil Thorne"],
    ["Cedar\nNone Removed or Discontinued", None],
    ["Dune", None],
    ["Mantle", "Okafor, D."],
]


def test_fldoe_parse_rows_pure_function():
    stats = Counter()
    assert list(fldoe.parse_rows(FL_ROWS, stats)) == [
        ("Birch", "The Lantern Keeper", "Dana Okafor"),
        ("Birch", "Hey, Neighbor: How I Found My Voice Second Line", "Wil Thorne"),
        ("Dune", "Mantle", "Okafor, D."),
    ]
    assert stats == {"districts_none_removed": 2, "districts_with_removals": 2}


def test_fldoe_district_carries_across_a_page_break():
    # A page break gives a new table that starts with titles and no district heading.
    rows = [["Title", "Author"], ["Birch", None], ["The Radio Orphans", "Ines Voss"], ["Quiet Machines", "Rei Nakamura"]]
    assert [d for d, _, _ in fldoe.parse_rows(rows)] == ["Birch", "Birch"]


def test_fldoe_title_before_any_district_raises():
    with pytest.raises(SourceFormatError):
        list(fldoe.parse_rows([["Title", "Author"], ["Mantle", "Okafor, D."]]))


def test_fldoe_load_makes_banned_district_records(monkeypatch):
    monkeypatch.setattr(fldoe, "table_rows", lambda path: FL_ROWS)
    records = fldoe.load("no-such-file.pdf", "fldoe_2024-25", school_year="2024-25", pdf_url=PDF_URL)
    assert len(records) == 3
    assert all(tuple(r) == RECORD_KEYS for r in records)
    assert {(r["state"], r["status"], r["scope"], r["school_year"], r["source_url"]) for r in records} == {
        ("FL", "banned", "district", "2024-25", PDF_URL)}
    assert [r["source_row"] for r in records] == ["1", "2", "3"]
    assert {r["district_name"] for r in records} == {"Birch", "Dune"}      # "None Removed" districts give no rows
    assert records[2]["title"] == "Mantle" and records[2]["author"] == "Okafor, D."


def test_fldoe_load_without_a_header_row_raises(monkeypatch):
    monkeypatch.setattr(fldoe, "table_rows", lambda path: [["Birch", None], ["Mantle", "Okafor, D."]])
    with pytest.raises(SourceFormatError, match="Title / Author"):
        fldoe.load("no-such-file.pdf", "fldoe_x", school_year="2024-25")


def test_fldoe_load_with_no_removed_titles_raises(monkeypatch):
    monkeypatch.setattr(fldoe, "table_rows", lambda path: [["Title", "Author"], ["Alder\nNone Removed or Discontinued", None]])
    with pytest.raises(SourceFormatError, match="no removed titles"):
        fldoe.load("no-such-file.pdf", "fldoe_x", school_year="2024-25")


def test_fldoe_wrong_layout_raises(tmp_path):
    # A PDF page with no Title/Author table must fail clearly. This stub has one empty page.
    stub = tmp_path / "stub.pdf"
    stub.write_bytes(
        b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    )
    with pytest.raises(Exception):
        fldoe.load(stub, "fldoe_x", school_year="2025-26")


# Extra check on the whole real PDF. It skips when the file is not in sources/.
@pytest.mark.skipif(not PDF.exists(), reason="sources/fldoe_2024-25_SDRPS.pdf is not in this checkout. "
                                            "See the README section 'Get the source files'.")
def test_fldoe_real_pdf_layout_and_totals():
    records = fldoe.load(PDF, "fldoe_2024-25", school_year="2024-25", pdf_url=PDF_URL)
    assert len(records) == 444
    assert len({r["district_name"] for r in records}) == 16
    assert tuple(records[0]) == RECORD_KEYS
    assert {(r["state"], r["status"], r["scope"], r["school_year"], r["source_url"]) for r in records} == {
        ("FL", "banned", "district", "2024-25", PDF_URL)}
    assert all(r["title"] and r["author"] and r["district_name"] for r in records)


# ---------------------------------------------------------------- Magnusson statewide agencies

def statewide_row(agency, state, **extra):
    row = {
        "ID": "1", "New_Book_Author": "Brandt, Elise M.", "New_Book_Title": "A Crown of Salt Marsh",
        "Initial_Response": "Off Shelves", "Decision": "Banned/Removed", "Restrictions": "",
        "Library_Type": "School", "Month": "08", "Year": "2024", "State": state, "Overseeing_Agency": agency,
        "Links1": "", "Links2": "", "Links3": "", "Links4": "",
    }
    return {**row, **extra}


def test_magnusson_state_board_row_is_statewide():
    # Utah statewide removals are filed under the state board.
    record = magnusson.convert_row(statewide_row("Utah State Board of Education", "UT"), Counter())
    assert record["scope"] == "statewide"
    assert record["district_name"] == ""
    assert record["school_year"] == "2024-25"


def test_magnusson_state_in_parentheses_is_statewide():
    # South Carolina statewide removals use "South Carolina (State)".
    record = magnusson.convert_row(
        statewide_row("South Carolina (State)", "SC", ID="2", Month="12", Year="2024"), Counter())
    assert record["scope"] == "statewide"

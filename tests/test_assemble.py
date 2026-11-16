"""Tests for the assembler: merging ban records into ban events, and the output files.

The tests feed fake loaders and fake match functions, so no source file or cache is needed.
"""
import csv
import json

import pytest

from pipeline import assemble
from pipeline.loaders import make_record
from pipeline.normalize import book_id, book_key
from pipeline.sources import Source

LEAID = "4823640"


def rec(source_id="pen_2023-24", title="The Lantern Keeper", author="Okafor, Dana", state="TX",
        district="Pine Hollow ISD", year="2023-24", calendar="", status="banned", scope="district", url="", row="1", note=""):
    return make_record(source_id=source_id, source_row=row, source_url=url, title=title, author=author,
                       state=state, district_name=district, school_year=year, calendar_year=calendar,
                       status=status, scope=scope, note=note)


def swide(source_id="utah_usbe", title="Trinkets", author="Hartwell, Eleanor", state="UT", year="2024-25", **kwargs):
    """A statewide ban record."""
    return rec(source_id=source_id, title=title, author=author, state=state, district="", year=year,
               scope="statewide", **kwargs)


def make_sources(**records_by_id):
    """Return a registry with one fake source per ID. PEN is priority 1, FLDOE 2, Magnusson 3."""
    priority = {"pen_2023-24": 1, "fldoe_2024-25": 2, "magnusson": 3, "pen_state": 1,
                "utah_usbe": 1, "pen_2024-25_state": 2}
    sources = []
    for source_id, records in records_by_id.items():
        sources.append(Source(
            id=source_id, name=f"Name of {source_id}", url=f"https://example.org/{source_id}",
            path=f"sources/{source_id}.txt", loader=lambda path, sid, stats=None, _r=records, **kw: [dict(r) for r in _r],
            priority=priority[source_id]))
    return sources


@pytest.fixture
def root(tmp_path):
    """A scratch repo root. The fake loaders ignore file contents, but the files must exist."""
    (tmp_path / "sources").mkdir()
    for name in ("pen_2023-24", "fldoe_2024-25", "magnusson", "pen_state", "utah_usbe", "pen_2024-25_state"):
        (tmp_path / "sources" / f"{name}.txt").write_text("x", encoding="utf-8")
    return tmp_path


def katy_matcher(pairs, nces_csv, review_path, counts=None):
    """A fake district matcher: every "Pine Hollow" name matches one LEAID, the rest do not match."""
    return {pair: {"leaid": LEAID if "Pine Hollow" in pair[1] else "", "nces_name": "Pine Hollow ISD", "method": "fake", "score": 100}
            for pair in pairs}


def run(root, sources, matches=None, matcher=katy_matcher):
    return assemble.build(root, matches=matches or {}, district_matcher=matcher, sources=sources,
                          today="2026-11-15", quiet=True)


def events_of(result):
    return result["events"]


# ---------------------------------------------------------------- merging

def test_same_event_from_two_sources_becomes_one_event_with_two_sources(root):
    sources = make_sources(**{
        "pen_2023-24": [rec(district="Pine Hollow Independent School District")],
        "magnusson": [rec(source_id="magnusson", district="Pine Hollow ISD", url="https://news.example/story")],
    })
    events = events_of(run(root, sources))
    assert len(events) == 1
    assert events[0]["sources"] == [{"id": "pen_2023-24"}, {"id": "magnusson", "url": "https://news.example/story"}]
    assert events[0]["district"]["key"] == LEAID


def test_different_school_years_stay_separate_events(root):
    sources = make_sources(**{"pen_2023-24": [rec(year="2023-24"), rec(year="2024-25", row="2")]})
    assert len(events_of(run(root, sources))) == 2


def test_calendar_year_record_joins_matching_school_year(root):
    sources = make_sources(**{
        "pen_2023-24": [rec(year="2023-24")],
        "magnusson": [rec(source_id="magnusson", year="", calendar="2024")],   # spring 2024 is in 2023-24
    })
    events = events_of(run(root, sources))
    assert len(events) == 1 and events[0]["year"] == "2023-24"
    assert [s["id"] for s in events[0]["sources"]] == ["pen_2023-24", "magnusson"]


def test_calendar_year_record_prefers_the_later_school_year(root):
    sources = make_sources(**{
        "pen_2023-24": [rec(year="2023-24"), rec(year="2024-25", row="2")],
        "magnusson": [rec(source_id="magnusson", year="", calendar="2024")],   # fits 2023-24 and 2024-25
    })
    events = {e["year"]: e for e in events_of(run(root, sources))}
    assert set(events) == {"2023-24", "2024-25"}
    assert [s["id"] for s in events["2024-25"]["sources"]] == ["pen_2023-24", "magnusson"]
    assert [s["id"] for s in events["2023-24"]["sources"]] == ["pen_2023-24"]


def test_calendar_year_record_with_no_candidate_keeps_empty_year(root):
    sources = make_sources(**{
        "pen_2023-24": [rec(year="2023-24")],
        "magnusson": [rec(source_id="magnusson", year="", calendar="2019")],
    })
    years = sorted(e["year"] for e in events_of(run(root, sources)))
    assert years == ["", "2023-24"]


def test_calendar_year_does_not_merge_across_districts(root):
    sources = make_sources(**{
        "pen_2023-24": [rec(year="2023-24", district="Other ISD")],
        "magnusson": [rec(source_id="magnusson", year="", calendar="2024")],
    })
    assert len(events_of(run(root, sources))) == 2


def test_status_comes_from_the_highest_priority_source(root):
    pen_restricted = rec(status="restricted")
    mag_banned = rec(source_id="magnusson", status="banned")
    result = run(root, make_sources(**{"pen_2023-24": [pen_restricted], "magnusson": [mag_banned]}))
    assert events_of(result)[0]["status"] == "restricted"       # PEN (1) beats Magnusson (3)
    result = run(root, make_sources(**{"fldoe_2024-25": [rec(source_id="fldoe_2024-25", status="banned")],
                                       "magnusson": [rec(source_id="magnusson", status="restricted")]}))
    assert events_of(result)[0]["status"] == "banned"           # state DOE (2) beats Magnusson (3)


def test_same_source_rows_with_two_statuses_use_the_stronger_status(root):
    rows = [rec(source_id="magnusson", status="restricted", row="1"), rec(source_id="magnusson", status="banned", row="2")]
    assert events_of(run(root, make_sources(magnusson=rows)))[0]["status"] == "banned"


def test_exact_duplicates_in_one_source_are_dropped_and_counted(root):
    rows = [rec(source_id="magnusson", row="1", url="a"), rec(source_id="magnusson", row="2", url="b")]
    result = run(root, make_sources(magnusson=rows))
    assert result["stats"]["duplicates_in_source:magnusson"] == 1
    assert len(result["records"]) == 1


def test_statewide_event_has_null_district(root):
    statewide = rec(source_id="pen_state", scope="statewide", district="", year="2024-25", state="UT")
    result = run(root, make_sources(pen_state=[statewide]))
    assert events_of(result)[0]["district"] is None
    state_file = result["files"]["UT"]
    assert state_file["bans"][0]["district"] is None and state_file["bans"][0]["scope"] == "statewide"
    assert state_file["districts"] == {}


def test_empty_district_name_becomes_unknown_district(root):
    result = run(root, make_sources(magnusson=[rec(source_id="magnusson", district="")]))
    file = result["files"]["TX"]
    assert file["bans"][0]["district"] == "n:unknown"
    assert file["districts"]["n:unknown"] == {"name": "Unknown district", "leaid": None}


# ---------------------------------------------------------------- books

def test_high_confidence_match_uses_the_work_id(root):
    key = book_key("The Lantern Keeper", "Okafor, Dana")
    match = {"key": key, "work": "OL1W", "ol_title": "The Lantern Keeper: A Memoir", "ol_authors": "Dana Okafor|Someone Else",
             "title_score": "95", "author_match": "1", "confidence": "high", "isbn": "9780000000002",
             "cover_id": "123", "ia_id": "lanternkeeper00okaf"}
    result = run(root, make_sources(**{"pen_2023-24": [rec()]}), matches={key: match})
    book = result["files"]["TX"]["books"]["OL1W"]
    assert book == {"title": "The Lantern Keeper: A Memoir", "authors": ["Dana Okafor", "Someone Else"], "ol": "OL1W",
                    "isbn": "9780000000002", "cover": "https://covers.openlibrary.org/b/id/123-M.jpg", "scan": True}


def test_low_confidence_match_is_not_used_and_goes_to_review(root):
    key = book_key("The Lantern Keeper", "Okafor, Dana")
    match = {"key": key, "work": "OL9W", "ol_title": "Other Book", "ol_authors": "Dana Okafor", "title_score": "60",
             "author_match": "1", "confidence": "low", "isbn": "1", "cover_id": "9", "ia_id": ""}
    sources = make_sources(**{"pen_2023-24": [rec()], "magnusson": [rec(source_id="magnusson", district="Other", row="2")]})
    result = run(root, sources, matches={key: match})
    expected_id = book_id("", key)
    book = result["files"]["TX"]["books"][expected_id]
    assert book == {"title": "The Lantern Keeper", "authors": ["Okafor, Dana"], "ol": None, "isbn": None, "cover": None, "scan": False}
    with open(root / "review" / "low_confidence_books.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["key"] == key and rows[0]["work"] == "OL9W" and rows[0]["ban_records"] == "2"


def test_unmatched_book_uses_spelling_of_highest_priority_source(root):
    sources = make_sources(**{
        "magnusson": [rec(source_id="magnusson", title="the lantern keeper", author="Dana Okafor", district="A")],
        "pen_2023-24": [rec(title="The Lantern Keeper", author="Okafor, Dana", district="B")],
    })
    result = run(root, sources)
    (book,) = result["files"]["TX"]["books"].values()
    assert book["title"] == "The Lantern Keeper" and book["authors"] == ["Okafor, Dana"]


# ---------------------------------------------------------------- output files

def test_state_file_shape_and_content(root):
    sources = make_sources(**{
        "pen_2023-24": [rec(title="Zed", author="A, B"), rec(title="Alpha", author="C, D", row="2"),
                        rec(state="UT", district="Birch", title="Other", author="E, F", row="3")],
    })
    result = run(root, sources)
    path = root / "data" / "states" / "TX.json"
    text = path.read_text(encoding="utf-8")
    assert text.startswith('{"state":"TX","name":"Texas","books":{')       # compact separators
    assert '"districts":{"4823640":{"name":"Pine Hollow ISD","leaid":"4823640"}}' in text
    data = json.loads(text)
    assert list(data) == ["state", "name", "books", "districts", "bans"]
    assert data["state"] == "TX" and data["name"] == "Texas"
    assert data["districts"] == {LEAID: {"name": "Pine Hollow ISD", "leaid": LEAID}}
    titles = [data["books"][b["book"]]["title"] for b in data["bans"]]
    assert titles == ["Alpha", "Zed"]                                      # sorted by book title
    assert set(data["bans"][0]) == {"book", "district", "year", "status", "scope", "sources"}
    assert "Other" not in text                                             # the UT book stays out of the TX file
    assert (root / "data" / "states" / "UT.json").exists()


def test_unmatched_district_key_and_index_file(root):
    sources = make_sources(**{"pen_2023-24": [rec(district="Some District", title="B1", author="A, A", row="1"),
                                              rec(source_id="pen_2023-24", scope="statewide", district="", title="B2",
                                                  author="A, A", row="2", year="2024-25")]})
    result = run(root, sources)
    data = result["files"]["TX"]
    assert data["districts"] == {"n:some district": {"name": "Some District", "leaid": None}}
    index = json.loads((root / "data" / "index.json").read_text(encoding="utf-8"))
    assert index["generated"] == "2026-11-15"
    assert index["states"]["TX"] == {"name": "Texas", "bans": 2, "books": 2, "statewide": 1}
    assert index["sources"] == {"pen_2023-24": {"name": "Name of pen_2023-24", "url": "https://example.org/pen_2023-24"}}


def test_csv_tables_and_counts_are_written(root):
    sources = make_sources(**{"pen_2023-24": [rec()], "magnusson": [rec(source_id="magnusson", url="https://n.example/x")]})
    run(root, sources)
    with open(root / "data" / "bans.csv", encoding="utf-8", newline="") as handle:
        (ban,) = list(csv.DictReader(handle))
    assert ban["sources"] == "pen_2023-24;magnusson" and ban["source_urls"] == ";https://n.example/x"
    assert ban["leaid"] == LEAID and ban["district_name"] == "Pine Hollow ISD" and ban["scope"] == "district"
    with open(root / "data" / "books.csv", encoding="utf-8", newline="") as handle:
        (book,) = list(csv.DictReader(handle))
    assert book["scan"] == "0" and book["ol_work"] == ""
    with open(root / "review" / "counts.csv", encoding="utf-8", newline="") as handle:
        counts = {(r["section"], r["name"]): r for r in csv.DictReader(handle)}
    assert counts[("source", "magnusson")]["ban_records"] == "1" and counts[("source", "magnusson")]["ban_events"] == "1"
    assert counts[("summary", "records_merged_into_other_events")]["value"] == "1"
    assert counts[("summary", "district_match_rate_by_record")]["value"] == "100.0%"


def test_missing_source_file_stops_the_build_with_a_clear_message(root):
    (root / "sources" / "magnusson.txt").unlink()
    sources = make_sources(**{"pen_2023-24": [rec()], "magnusson": [rec(source_id="magnusson")]})
    with pytest.raises(assemble.MissingSourceFile) as error:
        run(root, sources)
    message = str(error.value)
    assert "sources/magnusson.txt" in message                      # the missing file
    assert "https://example.org/magnusson" in message              # the page to download it from
    assert 'README section "Get the source files"' in message      # where to read the steps
    assert "pen_2023-24" not in message                            # a file that exists is not named
    assert not (root / "data").exists()                            # the build wrote nothing


def test_missing_source_message_lists_every_missing_file(root):
    (root / "sources" / "magnusson.txt").unlink()
    (root / "sources" / "pen_2023-24.txt").unlink()
    sources = make_sources(**{"pen_2023-24": [rec()], "magnusson": [rec(source_id="magnusson")]})
    with pytest.raises(assemble.MissingSourceFile) as error:
        run(root, sources)
    assert "sources/magnusson.txt" in str(error.value) and "sources/pen_2023-24.txt" in str(error.value)


def test_build_command_exits_with_the_missing_source_message(tmp_path, monkeypatch, capsys):
    # An empty repo root has none of the real registry's source files.
    from pipeline import __main__ as cli
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["build", "--no-match"]) == 1
    error = capsys.readouterr().err
    assert "sources/pen_2023-24_index.csv" in error
    assert "https://pen.org/report/beyond-the-shelves/" in error
    assert 'README section "Get the source files"' in error


def test_missing_other_module_gives_clear_error(root, monkeypatch):
    monkeypatch.setattr(assemble.importlib, "import_module", lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name=name)))
    with pytest.raises(assemble.MissingModule, match="pipeline/openlibrary.py"):
        assemble.build(root, sources=make_sources(**{"pen_2023-24": [rec()]}), quiet=True)


# ---------------------------------------------------------------- statewide merge rule

def test_statewide_records_merge_across_school_years(root):
    # Example: PEN dates "Trinkets" in Utah to 2024-03 (school year 2023-24). Utah's own list says 2025-03-06.
    sources = make_sources(**{
        "pen_2024-25_state": [swide(source_id="pen_2024-25_state", author="", year="2023-24")],
        "utah_usbe": [swide(year="2024-25")],
    })
    events = events_of(run(root, sources))
    assert len(events) == 1
    assert events[0]["year"] == "2024-25"                        # the Utah list (priority 1) sets the year
    assert [s["id"] for s in events[0]["sources"]] == ["utah_usbe", "pen_2024-25_state"]


def test_statewide_year_tie_takes_the_earliest_school_year(root):
    sources = make_sources(**{"utah_usbe": [swide(year="2025-26"), swide(year="2024-25", row="2")]})
    events = events_of(run(root, sources))
    assert len(events) == 1 and events[0]["year"] == "2024-25"


def test_statewide_year_ignores_an_empty_year_and_less_trusted_years(root):
    sources = make_sources(**{
        "utah_usbe": [swide(year="2025-26")],
        "magnusson": [swide(source_id="magnusson", year="2023-24"), swide(source_id="magnusson", year="", row="2")],
    })
    (event,) = events_of(run(root, sources))
    assert event["year"] == "2025-26"


def test_statewide_event_with_only_undated_records_has_empty_year(root):
    (event,) = events_of(run(root, make_sources(magnusson=[swide(source_id="magnusson", year="", calendar="2024")])))
    assert event["year"] == ""


def test_statewide_merge_keeps_different_states_and_books_apart(root):
    sources = make_sources(**{"utah_usbe": [swide(), swide(state="SC", row="2"), swide(title="Other", row="3")]})
    assert len(events_of(run(root, sources))) == 3


def test_statewide_and_district_events_stay_separate(root):
    sources = make_sources(**{"utah_usbe": [swide(title="The Lantern Keeper", author="Okafor, Dana", state="TX")],
                              "pen_2023-24": [rec(year="2024-25")]})
    assert len(events_of(run(root, sources))) == 2


def test_district_events_still_split_by_school_year(root):
    sources = make_sources(**{"pen_2023-24": [rec(year="2023-24"), rec(year="2024-25", row="2")]})
    assert sorted(e["year"] for e in events_of(run(root, sources))) == ["2023-24", "2024-25"]


# ---------------------------------------------------------------- note

def test_event_takes_the_note_of_its_most_trusted_record_that_has_one(root):
    sources = make_sources(**{
        "pen_2024-25_state": [swide(source_id="pen_2024-25_state", note="From PEN.")],
        "utah_usbe": [swide(note="")],
        "magnusson": [swide(source_id="magnusson", note="From Magnusson.", row="2")],
    })
    (event,) = events_of(run(root, sources))
    assert event["note"] == "From PEN."     # the Utah record (priority 1) has none, PEN (2) beats Magnusson (3)


def test_note_appears_in_state_file_only_when_not_empty_and_in_bans_csv(root):
    sources = make_sources(**{"utah_usbe": [swide(note="Grades 7-12."), swide(title="Other", row="2")]})
    result = run(root, sources)
    bans = {result["files"]["UT"]["books"][b["book"]]["title"]: b for b in result["files"]["UT"]["bans"]}
    assert bans["Trinkets"]["note"] == "Grades 7-12."
    assert "note" not in bans["Other"]
    with open(root / "data" / "bans.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert sorted(r["note"] for r in rows) == ["", "Grades 7-12."]


# ---------------------------------------------------------------- author fill

def test_author_fill_uses_same_state_records_and_the_most_trusted_spelling(root):
    sources = make_sources(**{
        "pen_2024-25_state": [swide(source_id="pen_2024-25_state", author="", year="2024-25")],
        "magnusson": [swide(source_id="magnusson", author="Eleanor Hartwell", row="2")],
        "utah_usbe": [swide(author="Hartwell, Eleanor", row="3")],
    })
    result = run(root, sources)
    assert {r["author"] for r in result["records"]} == {"Hartwell, Eleanor", "Eleanor Hartwell"}
    assert all(r["author"] for r in result["records"])
    filled = next(r for r in result["records"] if r["source_id"] == "pen_2024-25_state")
    assert filled["author"] == "Hartwell, Eleanor"
    assert result["stats"]["authors_filled"] == 1 and result["stats"]["authors_still_missing"] == 0


def test_author_fill_matches_on_main_title_and_ignores_case_and_series_note(root):
    sources = make_sources(**{
        "pen_2024-25_state": [swide(source_id="pen_2024-25_state", title="A Crown of Salt Marsh", author="")],
        "utah_usbe": [swide(title="A Crown Of Salt Marsh (Marsh Crown Saga #2)", author="Brandt, Elise M.")],
    })
    result = run(root, sources)
    assert all(r["author"] == "Brandt, Elise M." for r in result["records"])


def test_author_fill_same_state_conflict_falls_back_to_all_states_with_80_percent(root):
    # Utah has two surnames for "Ember". Across all states, 9 of 10 records say Zephyr (90%).
    others = [rec(source_id="magnusson", title="Ember", author="Zephyr", state="NY", district=f"D{i}", row=str(i)) for i in range(8)]
    sources = make_sources(**{
        "pen_2024-25_state": [swide(source_id="pen_2024-25_state", title="Ember", author="")],
        "magnusson": [swide(source_id="magnusson", title="Ember", author="Zephyr", row="1"),
                      swide(source_id="magnusson", title="Ember", author="Somebody Else", row="2")] + others,
    })
    result = run(root, sources)
    filled = next(r for r in result["records"] if r["source_id"] == "pen_2024-25_state")
    assert filled["author"] == "Zephyr"


def test_author_fill_leaves_the_author_empty_below_80_percent_and_reports_it(root):
    # Candidates: 3 records say Zephyr and 2 say Other, so the top share is 60%.
    cands = [rec(source_id="magnusson", title="Ember", author="Zephyr", state="NY", district=f"D{i}", row=str(i)) for i in range(3)]
    cands += [rec(source_id="magnusson", title="Ember", author="Other, Ann", state="NY", district=f"E{i}", row=str(10 + i)) for i in range(2)]
    sources = make_sources(**{
        "pen_2024-25_state": [swide(source_id="pen_2024-25_state", title="Ember", author="")],
        "magnusson": cands,
    })
    result = run(root, sources)
    assert result["stats"]["authors_filled"] == 0 and result["stats"]["authors_still_missing"] == 1
    with open(root / "review" / "missing_authors.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["source_id"], r["title"], r["state"]) for r in rows] == [("pen_2024-25_state", "Ember", "UT")]


def test_author_fill_with_no_candidate_is_reported(root):
    result = run(root, make_sources(magnusson=[rec(source_id="magnusson", title="Quiet Harbor (Film)", author="")]))
    assert result["stats"]["authors_still_missing"] == 1
    with open(root / "review" / "missing_authors.csv", encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 1


def test_author_fill_changes_the_book_key_so_the_ban_joins_the_book(root):
    sources = make_sources(**{
        "pen_2024-25_state": [swide(source_id="pen_2024-25_state", author="", year="2023-24")],
        "utah_usbe": [swide()],
    })
    result = run(root, sources)
    assert len(result["books"]) == 1 and len(result["events"]) == 1


def test_main_title_lives_in_normalize_and_openlibrary_keeps_the_old_name():
    from pipeline import normalize, openlibrary
    assert normalize.main_title("Trinkets (Trinkets, Book 1)") == "Trinkets "
    assert openlibrary.main_title is normalize.main_title

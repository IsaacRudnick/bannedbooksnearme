"""Loader for the Book Censorship Database by Dr. Tasslyn Magnusson (CSV export).

Reads: sources/magnusson_2025-08.csv.
Returns: ban records for school rows whose Decision means a ban or restriction.
The district name is the `Overseeing_Agency` column. The row ID is `source_row`.
The loader adds counts of kept and dropped rows to `stats` (a Counter).
"""
import csv
from collections import Counter
import re

from pipeline.loaders import SourceFormatError, check_header, make_record, school_year_label
from pipeline.normalize import state_code

REQUIRED_COLUMNS = (
    "ID", "New_Book_Author", "New_Book_Title", "Initial_Response", "Decision", "Restrictions",
    "Library_Type", "Month", "Year", "State", "Overseeing_Agency",
    "Links1", "Links2", "Links3", "Links4",
)

# Decision text (lowercase, trimmed) -> our status. README cites this table.
# The doubled values are typos in the export (two cells pasted into one).
# A Decision not in this table, and not in PENDING_DECISIONS, is dropped:
# Retained, Reshelved, Returned, No Action, Weeded, Unknown, N/A, blank, Stickered,
# Lost, Withdrawn, Unclear, and junk text.
DECISION_MAP = {
    "banned/removed": "banned",
    "banned/removedbanned/removed": "banned",
    "banned": "banned",
    "banned/do not order": "banned",
    "banned from library": "banned",
    "removed": "banned",
    "removed - curriculum": "banned",
    "retained/restricted": "restricted",
    "retained restricted": "restricted",
    "challenged - removed while under review": "banned_pending_investigation",
}

# These Decision values mean "review not finished". They count as
# banned_pending_investigation only when Initial_Response is "Off Shelves".
PENDING_DECISIONS = {"still in process", "still in processstill in process"}

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def map_decision(decision, initial_response):
    """Return our status for a row, or "" if the row does not count as a ban."""
    key = (decision or "").strip().lower()
    if key in DECISION_MAP:
        return DECISION_MAP[key]
    if key in PENDING_DECISIONS and (initial_response or "").strip().lower() == "off shelves":
        return "banned_pending_investigation"
    return ""


def month_number(text):
    """Return a month 1-12 from '06', '6', or 'June'. Return 0 if not a month."""
    text = (text or "").strip().lower()
    if text.isdigit() and 1 <= int(text) <= 12:
        return int(text)
    return MONTH_NAMES.get(text, 0)


def school_year_for(month, year):
    """Return (school_year, calendar_year) from the Month and Year columns.

    Year "2223" means the school year 2022-23. A Month like "22-23" or "SY22-23"
    also names a school year. Else we use the month: July to December starts a
    school year, January to June ends one. Fall and Spring work the same way.
    Summer, Winter, or an unknown month give school_year "" and keep the year.
    """
    month, year = (month or "").strip(), (year or "").strip()
    pair = re.search(r"(\d{2})\s*-\s*(\d{2})$", month)
    if pair and int(pair.group(2)) == (int(pair.group(1)) + 1) % 100:
        return school_year_label(2000 + int(pair.group(1))), ""
    if re.fullmatch(r"\d{4}", year):
        first, second = int(year[:2]), int(year[2:])
        # 2021 is a real year, so only treat 22xx and later as a two-year code.
        if first >= 22 and second == (first + 1) % 100:
            return school_year_label(2000 + first), ""
    if not re.fullmatch(r"\d{4}", year):
        return "", ""
    y = int(year)
    number = month_number(month)
    if number >= 7 or month.lower() == "fall":
        return school_year_label(y), year
    if 1 <= number <= 6 or month.lower() == "spring":
        return school_year_label(y - 1), year
    return "", year


def first_link(row):
    """Return the first Links1..Links4 value that is a web address.

    Some cells hold notes such as "PDF Files" or "Paper File". The site would
    show them as broken links, so we skip them.
    """
    for name in ("Links1", "Links2", "Links3", "Links4"):
        value = row[name].strip()
        if value.lower().startswith(("http://", "https://")):
            return value
    return ""


# A removal by a state board of education applies to every school in that state.
# The export records Utah's statewide removals this way ("Utah State Board of Education").
# South Carolina's statewide removals appear as "South Carolina (State)".
STATEWIDE_AGENCY = re.compile(r"\bstate (board|department) of education\b|\(state\)", re.IGNORECASE)


def convert_row(row, stats):
    """Return a ban record for one row, or None. Count why a row was dropped."""
    library_type = row["Library_Type"].strip()
    if library_type != "School":
        stats[f"dropped_library_type:{library_type or '(blank)'}"] += 1
        return None
    decision = row["Decision"].strip()
    status = map_decision(decision, row["Initial_Response"])
    if not status:
        stats["dropped_decision"] += 1
        stats[f"dropped_decision_value:{decision or '(blank)'}"] += 1
        if decision.lower() == "retained" and row["Restrictions"].strip().lower() == "yes":
            # TODO: decide with the owner whether these count as restricted. Report only for now.
            stats["retained_with_restrictions_yes (not included)"] += 1
        return None
    state = state_code(row["State"])
    if not state:
        # Example: "US" is the Defense Department schools, which have no state.
        stats[f"dropped_unknown_state:{row['State'].strip() or '(blank)'}"] += 1
        return None
    school_year, calendar_year = school_year_for(row["Month"], row["Year"])
    agency = row["Overseeing_Agency"].strip()
    statewide = bool(STATEWIDE_AGENCY.search(agency))
    if statewide:
        stats["kept_statewide"] += 1
    stats[f"kept:{status}"] += 1
    stats[f"kept_decision:{decision}"] += 1
    return make_record(
        source_id="", source_row=row["ID"], source_url=first_link(row),
        title=row["New_Book_Title"].strip(), author=row["New_Book_Author"].strip(),
        state=state, district_name="" if statewide else agency,
        school_year=school_year, calendar_year=calendar_year,
        status=status, scope="statewide" if statewide else "district",
    )


def load(path, source_id, stats=None, **_ignored):
    """Read the Magnusson CSV and return ban records."""
    stats = stats if stats is not None else Counter()
    records = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        check_header(path, reader.fieldnames, REQUIRED_COLUMNS)
        for row in reader:
            record = convert_row(row, stats)
            if record:
                record["source_id"] = source_id
                records.append(record)
    if not records:
        raise SourceFormatError(f"{path}: no ban rows found")
    return records

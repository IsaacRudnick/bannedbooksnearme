"""Loader for the PEN America Index of State Mandated Book Bans (CSV).

Reads: sources/pen_2024-25_state_mandated.csv. Columns: State, District, Title,
Date of Challenge/Removal. The file has no author column. The assembler fills
the authors later from other records (see "Author fill" in docs/DESIGN.md).
Returns: one statewide ban record for each row. The status is always "banned".
The district is "" because a state board of education applies the ban to every district.
The date is ISO, such as 2025-05-01. July to December starts a school year, and
January to June ends one. So 2025-05-01 is in 2024-25.
The loader removes two quirks from the titles:
- Zero-width characters, as in "A <U+200B>Court of Silver Flames".
- PEN's author initials in parentheses at the end, as in "Lucky (AS)" and "Tilt (EH)".
"""
import csv
import re
from datetime import date

from pipeline.loaders import SourceFormatError, check_header, make_record, remove_zero_width, school_year_of_date
from pipeline.normalize import state_code

REQUIRED_COLUMNS = ("State", "District", "Title", "Date of Challenge/Removal")

# PEN adds the author's initials to a title when two books share one title.
# The initials are 2 or 3 capital letters in parentheses at the end of the title.
INITIALS = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")


def clean_title(title):
    """Return the title without zero-width characters, author initials, and extra spaces."""
    title = re.sub(r"\s+", " ", remove_zero_width(title)).strip()
    return INITIALS.sub("", title).strip()


def load(path, source_id, source_url="", stats=None, **_ignored):
    """Read the state mandated CSV and return ban records."""
    records = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        check_header(path, reader.fieldnames, REQUIRED_COLUMNS)
        for number, row in enumerate(reader, start=1):
            state = state_code(row["State"])
            if not state:
                raise SourceFormatError(f"{path} row {number}: unknown state {row['State']!r}")
            try:
                day = date.fromisoformat(row["Date of Challenge/Removal"].strip())
            except ValueError:
                raise SourceFormatError(
                    f"{path} row {number}: date {row['Date of Challenge/Removal']!r} is not an ISO date") from None
            records.append(make_record(
                source_id=source_id, source_row=number, source_url=source_url,
                title=clean_title(row["Title"]), author="", state=state, district_name="",
                school_year=school_year_of_date(day.year, day.month), calendar_year=day.year,
                status="banned", scope="statewide",
            ))
    return records

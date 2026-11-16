"""Loader for a PEN America title-level index CSV (one row per title per district).

Reads: a CSV such as sources/pen_2023-24_index.csv.
Returns: ban records. The school year comes from the registry, because the file
does not state it. Later PEN index years should use this loader too. Check the
header first: the loader stops with a clear error if a column is missing.
The loader drops rows whose State is "Nation". These are Department of Defense
schools, which have no US state. The loader counts them in `stats`.
"""
import csv
import re
from collections import Counter

from pipeline.loaders import SourceFormatError, check_header, make_record
from pipeline.normalize import state_code

REQUIRED_COLUMNS = ("Title", "Author", "State", "District", "Date of Challenge/Removal", "Ban Status")

# PEN's "Ban Status" text -> our status. README cites this table.
# Any other text raises an error, because a guess here would put wrong data on the site.
STATUS_MAP = {
    "banned": "banned",
    "banned pending investigation": "banned_pending_investigation",
    "banned by restriction": "restricted",
    # PEN counts these rows in its ban index, so we count them as banned.
    "banned - professionally weeded": "banned",
}

# A State value that is not a US state, and that we drop on purpose.
NO_STATE = "Nation"


def map_status(text):
    """Return our status for a PEN status text. Raise SourceFormatError if unknown."""
    try:
        return STATUS_MAP[(text or "").strip().lower()]
    except KeyError:
        raise SourceFormatError(f"Unknown PEN Ban Status: {text!r}") from None


def year_from_date(text):
    """Return the 4-digit year in 'June 2024' or 'Fall 2023'. Return "" if none."""
    match = re.search(r"\b(19|20)\d{2}\b", text or "")
    return match.group(0) if match else ""


def load(path, source_id, school_year, stats=None, **_ignored):
    """Read a PEN index CSV and return ban records."""
    stats = stats if stats is not None else Counter()
    records = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        check_header(path, reader.fieldnames, REQUIRED_COLUMNS)
        for number, row in enumerate(reader, start=1):
            if row["State"].strip() == NO_STATE:
                stats[f"dropped_state:{NO_STATE}"] += 1
                continue
            state = state_code(row["State"])
            if not state:
                raise SourceFormatError(f"{path} row {number}: unknown state {row['State']!r}")
            records.append(make_record(
                source_id=source_id,
                source_row=number,
                title=row["Title"].strip(),
                author=row["Author"].strip(),
                state=state,
                district_name=row["District"].strip(),
                school_year=school_year,
                calendar_year=year_from_date(row["Date of Challenge/Removal"]),
                status=map_status(row["Ban Status"]),
                scope="district",
            ))
    return records

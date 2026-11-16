"""Loaders turn one source file into ban records.

A ban record is a dict of strings with exactly the keys in RECORD_KEYS.
The keys come from docs/DESIGN.md. The key `note` is optional and defaults to "".
Each loader module has one `load` function.
Every `load` function takes an optional `stats` Counter. It adds counts of
dropped rows to the Counter, so the build report can show them.
"""

RECORD_KEYS = (
    "source_id", "source_row", "source_url", "title", "author", "state",
    "district_name", "school_year", "calendar_year", "status", "scope", "note",
)

STATUSES = ("banned", "banned_pending_investigation", "restricted")


class SourceFormatError(ValueError):
    """A source file does not have the layout its loader expects."""


def make_record(**fields):
    """Return a ban record. Missing keys become "". Unknown keys raise an error."""
    unknown = set(fields) - set(RECORD_KEYS)
    if unknown:
        raise KeyError(f"Unknown ban record keys: {sorted(unknown)}")
    return {key: str(fields.get(key, "") or "") for key in RECORD_KEYS}


def check_header(path, header, required):
    """Raise SourceFormatError if `header` lacks any column in `required`."""
    missing = [name for name in required if name not in (header or [])]
    if missing:
        raise SourceFormatError(f"{path}: missing expected columns {missing}. Found: {list(header or [])}")


def school_year_label(start_year):
    """Return "2023-24" for the start year 2023."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def school_year_of_date(year, month):
    """Return the school year that holds a date.

    July to December starts a school year. January to June ends one.
    Example: (2025, 3) gives "2024-25". (2024, 8) gives "2024-25".
    """
    return school_year_label(year if month >= 7 else year - 1)


ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))


def remove_zero_width(text):
    """Remove zero-width characters (U+200B, U+200C, U+200D, U+FEFF) from a text."""
    return (text or "").translate(ZERO_WIDTH)

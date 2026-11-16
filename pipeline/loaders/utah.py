"""Loader for the Utah State Board of Education list of titles removed statewide (XLSX).

Reads: sources/utah_statewide_removals.xlsx, sheet "Titles".
The USBE lists a title when enough Utah districts removed it. Rows above the
header are notes, so the loader finds the header row by its first cell, "Title".
Columns: Title, Author, Removed in Districts, Removed in Charters, Located in Grades,
Original Publication Date, Date threshold met.
Returns: one statewide ban record for each title. The status is always "banned".
- The title loses its trailing parenthetical series note: "Tricks (Tricks, Book 1)" becomes "Tricks".
- The school year comes from "Date threshold met": July to December starts a school
  year, and January to June ends one.
- The `note` says which districts (and charters) removed the book first, and the grades.
"""
import re
from datetime import date, datetime

import openpyxl

from pipeline.loaders import SourceFormatError, check_header, make_record, remove_zero_width, school_year_of_date

SHEET = "Titles"
REQUIRED_COLUMNS = ("Title", "Author", "Removed in Districts", "Removed in Charters",
                    "Located in Grades", "Date threshold met")

# One parenthetical group at the end of the title, such as "(Court of Thorns and Roses #1)".
SERIES_NOTE = re.compile(r"\s*\([^()]*\)\s*$")


def squash(value):
    """Return a cell as text with zero-width characters removed and spaces collapsed."""
    return re.sub(r"\s+", " ", remove_zero_width("" if value is None else str(value))).strip()


def clean_title(value):
    """Return the title without its trailing series note."""
    return SERIES_NOTE.sub("", squash(value)).strip()


def name_list(value):
    """Return a comma-separated cell as clean text, such as 'Alpine, Davis, Nebo'."""
    return ", ".join(part for part in (squash(p) for p in squash(value).split(",")) if part)


def make_note(districts, charters, grades):
    """Return the note text for one row. Leave out any part whose cell is empty."""
    parts = []
    if districts:
        parts.append(f"Removed by these Utah districts before the statewide rule applied: {districts}.")
    if charters:
        parts.append(f"Removed by these Utah charter schools: {charters}.")
    if grades:
        parts.append(f"Grades {grades}.")
    return " ".join(parts)


def as_date(value, where):
    """Return a date from an Excel date cell. Accept ISO text too."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(squash(value))
    except ValueError:
        raise SourceFormatError(f"{where}: 'Date threshold met' {value!r} is not a date") from None


def find_header(rows, path):
    """Return (index in rows, header names) for the row whose first cell is "Title"."""
    for index, row in enumerate(rows):
        if row and squash(row[0]) == "Title":
            return index, [squash(cell) for cell in row]
    raise SourceFormatError(f"{path}: no header row that starts with 'Title' found")


def load(path, source_id, source_url="", stats=None, **_ignored):
    """Read the Utah XLSX and return ban records."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if SHEET not in workbook.sheetnames:
            raise SourceFormatError(f"{path}: no sheet named {SHEET!r}. Found: {workbook.sheetnames}")
        rows = list(workbook[SHEET].iter_rows(values_only=True))
    finally:
        workbook.close()
    start, header = find_header(rows, path)
    check_header(path, header, REQUIRED_COLUMNS)
    column = {name: header.index(name) for name in REQUIRED_COLUMNS}
    records = []
    for index in range(start + 1, len(rows)):
        row = rows[index]
        cells = {name: (row[i] if i < len(row) else None) for name, i in column.items()}
        title = clean_title(cells["Title"])
        if not title:
            continue
        number = index + 1  # The spreadsheet row number, which starts at 1.
        day = as_date(cells["Date threshold met"], f"{path} row {number}")
        records.append(make_record(
            source_id=source_id, source_row=number, source_url=source_url,
            title=title, author=squash(cells["Author"]), state="UT", district_name="",
            school_year=school_year_of_date(day.year, day.month), calendar_year=day.year,
            status="banned", scope="statewide",
            note=make_note(name_list(cells["Removed in Districts"]), name_list(cells["Removed in Charters"]),
                           squash(cells["Located in Grades"])),
        ))
    if not records:
        raise SourceFormatError(f"{path}: no title rows found below the header")
    return records

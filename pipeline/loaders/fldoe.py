"""Loader for the Florida Department of Education report of removed materials (PDF).

Reads: sources/fldoe_2024-25_SDRPS.pdf.
Layout of the PDF: one table per page with two columns, Title and Author.
A row with text in one cell only is a heading. A heading is either a district
name (for example "Bay"), a grade band ("Grades 9-12"), or a district name
followed by "None Removed or Discontinued". A row with two cells is one title.
A district continues across page breaks. The PDF lists only removals, so every
title becomes one ban record with status "banned".
"""
import re
from collections import Counter

import pdfplumber

from pipeline.loaders import SourceFormatError, make_record

NONE_REMOVED = "None Removed or Discontinued"


def clean(cell):
    """Join wrapped lines in a cell and collapse spaces."""
    return re.sub(r"\s+", " ", cell or "").strip()


def parse_rows(rows, stats=None):
    """Yield (district, title, author) for each removed title in the table rows.

    `rows` is a list of [cell, cell] lists in page order. This function has no
    PDF code in it, so tests can feed it plain lists.
    """
    stats = stats if stats is not None else Counter()
    district = ""
    started = False  # Rows before the ["Title", "Author"] row are the report's intro text.
    for row in rows:
        first, second = clean(row[0]), clean(row[1]) if len(row) > 1 else ""
        if not started:
            started = (first, second) == ("Title", "Author")
            continue
        if (first, second) == ("Title", "Author") or not first:
            continue
        if not second:
            if first.startswith("Grades "):
                continue
            if first.endswith(NONE_REMOVED):
                stats["districts_none_removed"] += 1
                district = ""
                continue
            district = first
            stats["districts_with_removals"] += 1
            continue
        if not district:
            raise SourceFormatError(f"FLDOE PDF: title {first!r} appears before any district heading")
        yield district, first, second


def table_rows(pdf_path):
    """Return every table row of the PDF, in page order."""
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows.extend(table)
    return rows


def load(path, source_id, school_year, pdf_url="", stats=None, **_ignored):
    """Read the Florida PDF and return ban records."""
    stats = stats if stats is not None else Counter()
    rows = table_rows(path)
    if not any(clean(r[0]) == "Title" for r in rows):
        raise SourceFormatError(f"{path}: no 'Title / Author' header row found. The PDF layout changed.")
    records = []
    for number, (district, title, author) in enumerate(parse_rows(rows, stats), start=1):
        records.append(make_record(
            source_id=source_id, source_row=number, source_url=pdf_url,
            title=title, author=author, state="FL", district_name=district,
            school_year=school_year, calendar_year="", status="banned", scope="district",
        ))
    if not records:
        raise SourceFormatError(f"{path}: no removed titles found")
    return records

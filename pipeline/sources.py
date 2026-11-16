"""Registry of source files.

Each entry says where a source file lives, who published it, and which loader
reads it. The assembler walks this list. To add a source file, add one entry here.
`priority` settles a status conflict between sources: the lowest number wins.
It also picks the school year of a statewide ban and the note of a ban event.
Priority 1: PEN's school district index, and a state's own list of its statewide bans (Utah).
Priority 2: PEN's state mandated index, and a state education department's district
reports (Florida). Priority 3: Magnusson.
"""
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.loaders import fldoe, magnusson, pen, pen_state, utah

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Source:
    id: str                 # Short ID, used in data files. Example: "pen_2023-24".
    name: str               # Human-readable name shown on the site.
    url: str                # The publisher's page.
    path: str               # File path, relative to the repo root.
    loader: callable        # Function load(path, source_id, stats=..., **args).
    priority: int           # 1 = most trusted.
    args: dict = field(default_factory=dict)  # Extra arguments for the loader.


SOURCES = [
    Source(
        id="pen_2023-24",
        name="PEN America Index of School Book Bans, 2023-24",
        url="https://pen.org/report/beyond-the-shelves/",
        path="sources/pen_2023-24_index.csv",
        loader=pen.load, priority=1,
        args={"school_year": "2023-24"},
    ),
    Source(
        id="pen_2024-25",
        name="PEN America Index of School Book Bans, 2024-25",
        url="https://pen.org/book-bans/pen-america-index-of-school-book-bans-2024-2025/",
        path="sources/pen_2024-25_index.csv",
        loader=pen.load, priority=1,
        args={"school_year": "2024-25"},
    ),
    Source(
        id="pen_2024-25_state",
        name="PEN America Index of State Mandated Book Bans, 2024-25",
        url="https://pen.org/book-bans/pen-america-index-of-school-book-bans-2024-2025/",
        path="sources/pen_2024-25_state_mandated.csv",
        loader=pen_state.load, priority=2,
    ),
    Source(
        id="utah_usbe",
        name="Utah State Board of Education, titles removed statewide (entries to 2026-09-28)",
        url="https://www.schools.utah.gov/curr/librarymedia",
        path="sources/utah_statewide_removals.xlsx",
        loader=utah.load, priority=1,  # A state's own list of its statewide bans is the most trusted source for those bans.
    ),
    Source(
        id="fldoe_2024-25",
        name="Florida Department of Education, 2024-25 school district reports of removed materials",
        url="https://cdn.fldoe.org/file/5574/2425-SDRPS-100628-2.pdf",
        path="sources/fldoe_2024-25_SDRPS.pdf",
        loader=fldoe.load, priority=2,
        args={"school_year": "2024-25", "pdf_url": "https://cdn.fldoe.org/file/5574/2425-SDRPS-100628-2.pdf"},
    ),
    Source(
        id="fldoe_2025-26",
        name="Florida Department of Education, 2025-26 school district reports of removed materials",
        url="https://www.fldoe.org/file/5574/2526-SDRPS.pdf",
        path="sources/fldoe_2025-26_SDRPS.pdf",
        loader=fldoe.load, priority=2,
        args={"school_year": "2025-26", "pdf_url": "https://www.fldoe.org/file/5574/2526-SDRPS.pdf"},
    ),
    Source(
        id="magnusson",
        name="Book Censorship Database by Dr. Tasslyn Magnusson (export with entries to 2025-08-14)",
        url="https://www.everylibraryinstitute.org/book_censorship_database_magnusson",
        path="sources/magnusson_2025-08.csv",
        loader=magnusson.load, priority=3,
    ),
]


def by_id():
    """Return the registry as a dict keyed by source ID."""
    return {source.id: source for source in SOURCES}

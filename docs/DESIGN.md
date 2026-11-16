# Design: data pipeline and "near me" site

This document describes how the pipeline turns source files into the JSON files that the site reads. It is the contract between the pipeline modules and the page. Change this document when you change a file format.

## Terms

- **Source file**: a file in `sources/` that a person downloaded from a publisher (PEN America, Dr. Magnusson, a state education department). The pipeline never changes these files.
- **Raw input**: a large public file in `raw/` (Open Library dumps, NCES, Census, IMLS). Git ignores `raw/`. The README lists the download URL of each raw input.
- **Cache file**: a small CSV in `cache/` that the extract step makes from a raw input. Git tracks `cache/`. With the cache files, a person can rebuild the site from `sources/` without the raw inputs.
- **Ban record**: one row from a source file that counts as a ban. A loader makes ban records.
- **Ban event**: one book removed or restricted in one place. A place is a district or a whole state. The pipeline merges the ban records that describe the same ban into one ban event with a list of sources. A district ban event is one book in one district in one school year. A statewide ban event is one book in one state, in any school year. See "Merge rule for ban events" below.
- **Priority**: a number that each source has in `pipeline/sources.py`. The lowest number is the most trusted source. See "Source priority" below.
- **Book**: one Open Library work, or one unmatched title and author pair.
- **LEAID**: the 7-digit NCES ID of a school district (Local Education Agency).
- **ZCTA**: a Census ZIP Code Tabulation Area. The site treats a ZCTA as a ZIP code.

## Pipeline steps

The command `python -m pipeline` runs all steps in order. `python -m pipeline extract` runs only step 1.

1. **Extract** (`pipeline/openlibrary.py`, `pipeline/districts.py`, `pipeline/geo.py`). These modules read `raw/` and write `cache/`. A module skips its raw input when the cache already holds every row the build needs.
2. **Load** (`pipeline/loaders/*.py`). Each loader reads one source file and returns ban records. The assembler then fills empty authors. See "Author fill" below.
3. **Match books** (`pipeline/openlibrary.py`). The matcher gives each title and author pair an Open Library work and a confidence flag.
4. **Match districts** (`pipeline/districts.py`). The matcher gives each state and district name pair an LEAID, or none.
5. **Assemble** (`pipeline/assemble.py`). The assembler merges ban records into ban events. It writes `data/books.csv`, `data/bans.csv`, `data/index.json`, and `data/states/XX.json`.
6. **ZIP files** (`pipeline/geo.py`). The geo module writes `data/zips/NNN.json`.

## Ban record (loader output)

Each loader returns a list of dicts with these keys. All values are strings. An unknown value is `""`.

| Key | Example | Notes |
|---|---|---|
| `source_id` | `pen_2023-24` | One ID per source file. See `pipeline/sources.py`. |
| `source_row` | `412` | Row number or the source's own row ID. Used for review only. |
| `source_url` | `https://...` | A per-row link if the source gives one. Else `""`. |
| `title` | `Call Me By Your Name` | As the source writes it. |
| `author` | `Aciman, André` | As the source writes it. |
| `state` | `AK` | Two-letter code. |
| `district_name` | `Matanuska-Susitna Borough School District` | `""` for a statewide ban. |
| `school_year` | `2023-24` | `""` if the source does not say. |
| `calendar_year` | `2023` | Used to merge records that have no school year. |
| `status` | `banned` | One of `banned`, `banned_pending_investigation`, `restricted`. |
| `scope` | `district` | `district` or `statewide`. |
| `note` | `Removed by these Utah districts before the statewide rule applied: Alpine, Davis. Grades 7-12.` | Optional. Free text from the source. Default `""`. |

## Source priority

Each source has a priority number in `pipeline/sources.py`. The lowest number is the most trusted source. The priority settles three questions for one ban event: which status it has, which note it has, and (for a statewide ban event) which school year it has.

| Priority | Sources | Why |
|---|---|---|
| 1 | PEN school district index (`pen_2023-24`, `pen_2024-25`), Utah State Board of Education list (`utah_usbe`) | PEN checks each district ban. A state's own list of its statewide bans is the most trusted source for those bans. |
| 2 | PEN state mandated index (`pen_2024-25_state`), state education department reports (`fldoe_2024-25`, `fldoe_2025-26`) | The publisher is an authority, but the list has less detail. |
| 3 | Magnusson database (`magnusson`) | A volunteer database. It holds the most rows and the most errors. |

## Merge rule for ban events

The assembler merges ban records into ban events with these rules.

- **District records** merge when the book, the state, the district, and the school year are equal. A record with no school year joins an event for the same book and district whose school year holds the calendar year of the record. If two such events exist, the later one wins.
- **Statewide records** merge when the book and the state are equal. The school year plays no part. The reason: sources date one statewide ban in different years. For example, PEN dates "Tricks" in Utah to March 2024 (school year 2023-24). The Utah list dates it to March 2025 (school year 2024-25). These are one ban, so the pipeline makes one ban event.
- **Year of a statewide ban event**: the school year of its most trusted record. Records with no school year do not count. If several records share the best priority, the event takes the earliest school year among them.
- **Status of a ban event**: the status of the most trusted record. Within one source, the stronger status wins: `banned`, then `banned_pending_investigation`, then `restricted`.
- **Note of a ban event**: the note of the most trusted record that has a note. The state file shows `note` only when it is not empty. The file `data/bans.csv` has a `note` column that is empty when there is no note.

## Author fill

Some records have no author. The PEN state mandated file has no author column at all. The author is part of the book key, so a record with no author finds the wrong Open Library work or none. The assembler fills these authors after it loads and dedupes the records, and before it matches books.

1. For a record with no author, the assembler finds the other records that have an author and the same `norm_title(main_title(title))`. The `main_title` function cuts the title at the first `:`, ` - `, or `(`. So "Tricks (Tricks, Book 1)" and "Tricks" have the same main title.
2. The assembler looks at the records in the same state first. If all of them have the same first surname, it copies the author text of the most trusted one.
3. If the records of the same state do not agree, or there are none, the assembler looks at the records of all states. It needs a first surname that at least 80% of these records have. Then it copies the author text of the most trusted record with that surname. The 80% is a guess, marked `TODO:` in `pipeline/assemble.py` (`AUTHOR_FILL_SHARE`).
4. If neither step finds an author, the record stays with no author. The assembler writes it to `review/missing_authors.csv`.

Only records that had an author at the start give authors. A filled record never gives an author to another record. The report in `review/counts.csv` shows `authors_filled` and `authors_still_missing`.

## Open Library match (cache file `cache/ol_matches.csv`)

The matcher keys each row by `book_key(title, author)` from `pipeline/normalize.py`.

| Column | Meaning |
|---|---|
| `key` | The normalized title and author key. |
| `work` | Open Library work ID, for example `OL8034526W`. Empty if no candidate. |
| `ol_title`, `ol_authors` | The title and authors of the matched work. Authors are joined with `|`. |
| `title_score` | Title similarity from 0 to 100 after normalization. |
| `author_match` | `1` if a source author surname is in the work's author names. |
| `confidence` | `high`, `low`, or `none`. See below. |
| `isbn` | One ISBN-13 (or ISBN-10) from an edition of the work. |
| `cover_id` | Open Library cover ID. The cover URL is `https://covers.openlibrary.org/b/id/{cover_id}-M.jpg`. |
| `ia_id` | Internet Archive ID of a scanned edition, if one exists. |

- **high**: `title_score >= 90` and `author_match = 1`. The site uses the work's cover and link.
- **low**: a candidate exists but fails a test. The pipeline writes the row to `review/low_confidence_books.csv`. The site shows the ban with the source title and author, a placeholder cover, and no Open Library link.
- **none**: no candidate. The site shows the ban like a low match.

## Geography cache files

`pipeline/geo.py` writes these files during the extract step.

| File | Columns | Made from |
|---|---|---|
| `cache/nces_districts.csv` | leaid, state, name, lograde, higrade, funcstat, parent_leaid | The `.dbf` table in the NCES SY 2024-25 district file |
| `cache/zcta_districts.csv` | zcta, leaid, land_sqmi, share | `grf25_lea_zcta5ce20.xlsx` in NCES GRF25 |
| `cache/zcta_centroids.csv` | zcta, lat, lon | The Census 2025 ZCTA Gazetteer |
| `cache/library_outlets.csv` | id, name, address, city, state, zip, lat, lon, type | The IMLS PLS FY2024 outlet file |

- **funcstat**: NCES marks some rows as grade-band parts of a real district (funcstat F). The extract step points these rows to the real district in `parent_leaid`.
- **share**: the part of the ZIP code's land that the district covers. The ZIP files leave out a district with a share under 1% (`MIN_DISTRICT_SHARE`, a guess marked TODO). The largest district always stays.
- **type**: the IMLS outlet type. The extract step keeps CE (central library) and BR (branch). It drops bookmobiles, books-by-mail, and outlets that are closed for a time.
- A ZIP code with no school district takes the state of its nearest library. This affects 2 ZIP codes.

## District matching

`pipeline/districts.match_districts` tries these steps in order for each state and district name. It stops at the first step that finds exactly one NCES district in the same state.

1. **exact**: the normalized names are equal. Normalization folds the text and turns forms such as "Independent School District" into "isd".
2. **core**: the names are equal after the matcher removes district-type words and spaces. "Adel DeSoto Minburn" then matches "Adel-De Soto-Minburn".
3. **bare**: the core names are equal after the matcher removes district numbers and one-letter codes. The matcher refuses this step when the two names have different district numbers.
4. **fuzzy**: the rapidfuzz score is 94 or more, and it is at least 5 points above the second-best score. Both numbers are guesses marked TODO.

A name with no match goes to `review/unmatched_districts.csv` with its best candidate and score.

## Site files

### `data/index.json`

```json
{
  "generated": "2026-11-15",
  "states": {"TX": {"name": "Texas", "bans": 8123, "books": 2400, "statewide": 0}},
  "sources": {"pen_2023-24": {"name": "PEN America Index of School Book Bans, 2023-24", "url": "https://pen.org/report/beyond-the-shelves/"}}
}
```

### `data/states/XX.json`

```json
{
  "state": "TX",
  "name": "Texas",
  "books": {
    "OL8034526W": {"title": "Call Me By Your Name", "authors": ["André Aciman"], "ol": "OL8034526W",
                    "isbn": "9780374118044", "cover": "https://covers.openlibrary.org/b/id/123-M.jpg", "scan": true},
    "x3f2a9c01be": {"title": "Some Title", "authors": ["Doe, Jane"], "ol": null, "isbn": null, "cover": null, "scan": false}
  },
  "districts": {
    "4823640": {"name": "Katy ISD", "leaid": "4823640"},
    "n:some district": {"name": "Some District", "leaid": null}
  },
  "bans": [
    {"book": "OL8034526W", "district": "4823640", "year": "2023-24", "status": "banned", "scope": "district",
     "sources": [{"id": "pen_2023-24"}, {"id": "magnusson", "url": "https://..."}]},
    {"book": "OL8034526W", "district": null, "year": "2024-25", "status": "banned", "scope": "statewide",
     "note": "Removed by these Utah districts before the statewide rule applied: Alpine, Davis. Grades 7-12.",
     "sources": [{"id": "utah_usbe"}, {"id": "pen_2024-25_state"}]}
  ]
}
```

- The key `note` appears in a ban only when the note is not empty.
- A book ID is the Open Library work ID for a high match. Else it is `x` plus the first 10 hex digits of the SHA-1 of the book key.
- A district key is the LEAID when the matcher finds one. Else it is `n:` plus the normalized district name.

### `data/zips/NNN.json`

`NNN` is the first three digits of the ZIP code. One file holds every ZCTA with those digits.

```json
{
  "75001": {
    "states": ["TX"],
    "districts": [{"leaid": "4816230", "name": "Carrollton-Farmers Branch ISD", "state": "TX"}],
    "libraries": [{"name": "Addison Public Library", "address": "4700 Belt Line Rd", "city": "Addison",
                   "state": "TX", "zip": "75001", "miles": 0.8}]
  }
}
```

### `data/books_unbanned.json`

The build copies this file from `sources/books_unbanned.json`. A person writes the source file by hand. Each eligibility text is a quote from the library's own page.

```json
{"checked": "2026-11-15", "programs": [{"library": "Boston Public Library", "url": "https://www.bpl.org/books-unbanned/", "eligibility": "..."}]}
```

### How the page joins the files

- The page joins a ZIP district to a state-file district by `leaid` only. A district with an `n:` key has no LEAID, so it never shows as a ZIP district. Its bans show under "Other bans in <State>".
- A ZIP file can list a district that has no bans. That district is absent from the state file's `districts`. The page then shows "No bans recorded in <district>".

## Page behavior

1. A person clicks a state or types a ZIP code.
2. For a state click, the page fetches only `data/states/XX.json`.
3. For a ZIP code, the page fetches `data/zips/NNN.json`, then the state file of each state in `states`.
4. The page shows one section for each district that covers the ZIP code. Then it shows the statewide bans. Then it shows the rest of the state.
5. The page shows the 3 nearest public library outlets for a ZIP code.
6. Each state view links to the EveryLibrary bill tracker.

`docs/UI.md` describes the page layout, the URL routes, the filters, and the job of each function in `public/app.js`.

# Banned Books Near Me

This site lists books that schools removed or restricted, by state and by ZIP code. Type a ZIP code to see the bans in your school district first. The site then shows the statewide bans and the other bans in your state. It also shows the 3 nearest public libraries and the Books Unbanned programs, which give free digital library cards to teens and young adults.

The site is static. GitHub Pages serves it. A Python pipeline makes all the data files before you publish.

## Data sources

Each source keeps its own license. The project license (GPL v3) covers the code only. It is not a license to the data. The files here are a collection of these datasets for the single purpose of serving this site.

Git does not track the source files in `sources/`, because each publisher owns its data. Git tracks only `sources/books_unbanned.json`, which a person wrote by hand. You download each publisher file yourself. See "Get the source files" below. Git does track the processed files in `data/`, `cache/`, and `review/`. These files are our own extracts, tables, and checks. They do not copy the source files.

| Source | Save as | Owner | Used for |
|---|---|---|---|
| [PEN America Index of School Book Bans, 2023-24](https://pen.org/report/beyond-the-shelves/) | `sources/pen_2023-24_index.csv` | PEN America | District bans, July 2023 to June 2024 |
| [PEN America Index of School Book Bans, 2024-25](https://pen.org/book-bans/pen-america-index-of-school-book-bans-2024-2025/) | `sources/pen_2024-25_index.csv` | PEN America | District bans, July 2024 to June 2025 |
| [PEN America Index of State Mandated Book Bans, 2024-25](https://pen.org/book-bans/pen-america-index-of-school-book-bans-2024-2025/) | `sources/pen_2024-25_state_mandated.csv` | PEN America | Statewide bans in South Carolina and Utah. The file has no authors. |
| [Utah State Board of Education, titles removed statewide](https://www.schools.utah.gov/curr/librarymedia) | `sources/utah_statewide_removals.xlsx` (entries to 2026-09-28) | Utah State Board of Education | Statewide bans in Utah, with the districts that removed each book first |
| [Book Censorship Database by Dr. Tasslyn Magnusson](https://www.everylibraryinstitute.org/book_censorship_database_magnusson) | `sources/magnusson_2025-08.csv` (entries to 2025-08-14) | Dr. Tasslyn Magnusson, hosted by the EveryLibrary Institute | District and statewide bans, 2021 to 2025 |
| [Florida Department of Education, 2024-25 removed materials report](https://cdn.fldoe.org/file/5574/2425-SDRPS-100628-2.pdf) | `sources/fldoe_2024-25_SDRPS.pdf` | Florida Department of Education | Florida district removals, 2024-25 |
| [Florida Department of Education, 2025-26 removed materials report](https://www.fldoe.org/file/5574/2526-SDRPS.pdf) | `sources/fldoe_2025-26_SDRPS.pdf` | Florida Department of Education | Florida district removals, 2025-26 |
| [Open Library data dumps](https://openlibrary.org/developers/dumps) | `cache/ol_matches.csv` (extract) | Internet Archive (Open Library data is public domain, CC0) | Book titles, authors, ISBNs, and covers |
| [NCES EDGE school district boundaries, SY 2024-25](https://nces.ed.gov/programs/edge/data/EDGE_SCHOOLDISTRICT_TL25_SY2425.zip) | `cache/nces_districts.csv` (extract) | National Center for Education Statistics | District names and LEAIDs |
| [NCES EDGE Geographic Relationship Files 2025](https://nces.ed.gov/programs/edge/Geographic/RelationshipFiles) | `cache/zcta_districts.csv` (extract) | National Center for Education Statistics | ZIP code to school district |
| [Census 2025 Gazetteer, ZCTA file](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_zcta_national.zip) | `cache/zcta_centroids.csv` (extract) | U.S. Census Bureau | The center point of each ZIP code |
| [IMLS Public Libraries Survey, FY2024](https://www.imls.gov/research-evaluation/surveys/public-libraries-survey-pls) | `cache/library_outlets.csv` (extract) | Institute of Museum and Library Services | Public library locations |
| Books Unbanned program pages (links in the file) | `sources/books_unbanned.json` | Each library | Eligibility text, quoted from each library's page |
| [EveryLibrary bill tracker](https://www.everylibrary.org/billtracking) | Link only | EveryLibrary | State legislation |
| [simplemaps HTML5 US map](https://simplemaps.com/docs/selectable-states#usmap) | `public/usmap.js`, `public/mapdata.js` | simplemaps. This file is a trial build under the [simplemaps license](https://simplemaps.com/license), not an open-source license. | The state selector |

### Which PEN rows count as bans

PEN gives each row of its index a `Ban Status`. `pipeline/loaders/pen.py` holds the table. The loader stops with an error when it finds a status that is not in the table.

| Ban Status in the PEN index | Status on this site |
|---|---|
| Banned | Banned |
| Banned - Professionally Weeded | Banned |
| Banned Pending Investigation | Banned pending investigation |
| Banned by Restriction | Restricted |

PEN counts the "Professionally Weeded" rows in its ban index. So this site counts them as banned. The 2024-25 file has 10 such rows.

The 2024-25 PEN file has 590 rows with the State "Nation" and the district "Department of Defense Educational Activitity" (PEN's spelling). These are schools that the Department of Defense runs. They have no US state. The site shows bans by state and by ZIP code, so it cannot place these rows. The loader drops them and counts them as `dropped_state:Nation` in `review/counts.csv`. The loader still stops with an error for any other State value that is not a US state.

### Which Magnusson rows count as bans

The pipeline keeps a Magnusson row only if its `Library_Type` is `School` and its `Decision` means that the school removed or restricted the book. `pipeline/loaders/magnusson.py` holds the table.

| Decision in the export | Status on this site |
|---|---|
| Banned/Removed, Banned, Banned/Do Not Order, Banned From Library, Removed, Removed - Curriculum | Banned |
| Retained/Restricted, Retained Restricted | Restricted |
| Still in Process, when `Initial_Response` is "Off Shelves" | Banned pending investigation |
| Challenged - Removed While Under Review | Banned pending investigation |

The pipeline drops all other decisions. Examples are Retained, Reshelved, Returned, No Action, Weeded/Deselected, and Unknown. It also drops public and college library rows, because the site shows school bans by district. A row whose `Overseeing_Agency` is a state board of education, or a name such as "South Carolina (State)", is a statewide ban.

### Statewide bans

A statewide ban applies to every school district in its state. The site shows it for every ZIP code in that state, with a "Statewide" label.

Three sources list statewide bans: the PEN state mandated index, the Utah State Board of Education list, and Magnusson. Each source dates the same ban in a different month. So the pipeline merges all statewide records for one book in one state into one ban event, whatever their school years. The ban event takes the school year of its most trusted record. The source with the lowest priority number is the most trusted. If two records share the best priority, the ban event takes the earlier school year. For example, PEN dates "Tricks" in Utah to March 2024. The Utah list dates it to March 2025. The site shows one Utah ban for "Tricks", in the school year 2024-25, because the Utah list is the most trusted source. `docs/DESIGN.md` lists the priority of each source.

The Utah list also names the districts that removed each book before the statewide rule applied, and the grades. The pipeline keeps this text in a `note` field of the ban event.

### Missing authors

A few records have no author. The PEN state mandated file has no author column. The pipeline fills an empty author from other records with the same main title, which is the title before the first colon, dash, or parenthesis. It looks in the same state first. It copies the author of the most trusted record when those records agree on one first surname. If they do not agree, it looks in all states. It then needs at least 80% of the records to share one first surname. If it finds no author, the record keeps an empty author and goes to `review/missing_authors.csv`. The 80% is a guess that the owner can tune.

## Get the source files

The build needs 7 files in `sources/`. If one is missing, `python -m pipeline build` stops. It prints the name of the missing file and the page to download it from. It does not skip a missing file.

Follow these steps. Save each file in `sources/` with the exact name shown.

<!-- TODO: check the wording of steps 1 and 4 against the live PEN 2023-24 page and the Magnusson page. The owner downloaded those files by hand, and the agent did not see the download buttons. -->

1. **PEN 2023-24 index.** Open https://pen.org/report/beyond-the-shelves/. Download the index of school book bans as a CSV file. Save it as `sources/pen_2023-24_index.csv`.
2. **PEN 2024-25 index.** Open https://pen.org/book-bans/pen-america-index-of-school-book-bans-2024-2025/. The page links to a Google Sheet. Open the tab for the school book bans index. Choose File > Download > Comma-separated values. Save the file as `sources/pen_2024-25_index.csv`.
3. **PEN 2024-25 state mandated bans.** Open the same page as step 2. Open the Google Sheet tab for the state mandated bans. Choose File > Download > Comma-separated values. Save the file as `sources/pen_2024-25_state_mandated.csv`.
4. **Magnusson database.** Open https://www.everylibraryinstitute.org/book_censorship_database_magnusson. Download the database as a CSV file. Save it as `sources/magnusson_2025-08.csv`.
5. **Utah State Board of Education list.** Open https://www.schools.utah.gov/curr/librarymedia. Find the "Sensitive Materials" list of titles removed statewide. Download the spreadsheet and keep it as an `.xlsx` file. Save it as `sources/utah_statewide_removals.xlsx`.
6. **Florida 2024-25 report.** Download https://cdn.fldoe.org/file/5574/2425-SDRPS-100628-2.pdf. Keep it as a PDF file. Save it as `sources/fldoe_2024-25_SDRPS.pdf`.
7. **Florida 2025-26 report.** Download https://www.fldoe.org/file/5574/2526-SDRPS.pdf. Keep it as a PDF file. Save it as `sources/fldoe_2025-26_SDRPS.pdf`.

Do not open a CSV file in Excel and save it again. Excel changes some titles. For example, it changes "November 9" to "9-Nov". If you must look at a CSV file, open it and close it without saving. Or use a text editor.

Some publisher sites block automated downloads. Use a web browser to download the files.

The files in `data/`, `cache/`, and `review/` are processed files. Git tracks them. You do not need to download them.

## How to build the data

You need Python 3.11 or later.

```bash
pip install -r requirements.txt
python -m pipeline
```

The command runs two steps:

1. **extract** reads the large raw inputs in `raw/` and writes small CSV files in `cache/`. Git tracks `cache/`.
2. **build** reads `sources/` and `cache/`. It writes `data/` (the site files) and `review/` (files for a person to check).

If `cache/` already covers every title and district, you do not need `raw/`. Run `python -m pipeline build` to rebuild the site from `sources/` and `cache/` only. The extract step skips a raw input when the cache has every row that the build needs.

### Raw inputs

Git ignores `raw/`. Download these files only when the extract step asks for them.

| Save as | Download from | Size |
|---|---|---|
| `raw/openlibrary/ol_dump_works_latest.txt.gz` | https://openlibrary.org/data/ol_dump_works_latest.txt.gz | about 4 GB |
| `raw/openlibrary/ol_dump_authors_latest.txt.gz` | https://openlibrary.org/data/ol_dump_authors_latest.txt.gz | about 0.8 GB |
| `raw/openlibrary/ol_dump_editions_latest.txt.gz` | https://openlibrary.org/data/ol_dump_editions_latest.txt.gz | about 10 GB |
| `raw/nces/GRF25.zip` | https://nces.ed.gov/programs/edge/data/GRF25.zip | 48 MB |
| `raw/nces/EDGE_SCHOOLDISTRICT_TL25_SY2425.zip` | https://nces.ed.gov/programs/edge/data/EDGE_SCHOOLDISTRICT_TL25_SY2425.zip | 212 MB |
| `raw/census/2025_Gaz_zcta_national.zip` | https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_zcta_national.zip | 1 MB |
| `raw/imls/pls_fy2024_csv.zip` | https://www.imls.gov/sites/default/files/2026-06/pls_fy2024_csv.zip | 20 MB |

The pipeline does not call the Open Library API. Open Library asks bulk users to use the dumps instead.

### Output files

| File | What it holds |
|---|---|
| `data/books.csv` | One row per book: an Open Library work, or a title and author with no confident match. |
| `data/bans.csv` | One row per ban event: book, state, district, LEAID, school year, status, scope, sources, and an optional note. |
| `data/index.json` | State names, counts, and source names. The page loads it first. |
| `data/states/XX.json` | The books, districts, and ban events of one state. The page loads one file per state. |
| `data/zips/NNN.json` | The districts and the 3 nearest libraries of each ZIP code that starts with NNN. |
| `review/low_confidence_books.csv` | Titles whose Open Library match failed the title or author test. The site shows these with the source title and no Open Library link. |
| `review/missing_authors.csv` | Ban records that have no author, and that the author fill could not fix. |
| `review/unmatched_districts.csv` | District names with no NCES LEAID. Their bans show under "Other bans" in the state. |
| `review/counts.csv` | Counts per source, state, and school year, and the match rates. |

`docs/DESIGN.md` describes every file format and the matching rules. `docs/UI.md` describes the page.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python tools/check_site.py OUT_DIR 78217 84101 32792
```

The tests do not need any file in `sources/`. The loader tests use made-up rows in the real column layouts of the source files. The titles, authors, and districts in these rows are invented. A few extra tests read the whole real files. Each of these tests skips, with a reason, when its file is missing from `sources/`.

`tools/check_site.py` serves the site, opens it in headless Microsoft Edge, clicks Texas, and looks up three ZIP codes. It saves screenshots in `OUT_DIR` and prints every console error.

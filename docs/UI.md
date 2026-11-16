# The page: layout, routes, filters, and code

This document explains the front end of Banned Books Near Me. The front end is plain HTML, CSS, and JavaScript. It has no build step. It reads the JSON files that `docs/DESIGN.md` describes.

## Files

- **`index.html`** holds the page structure. It has no data in it.
- **`public/app.css`** holds all styles. Every color is a CSS custom property at the top of the file.
- **`public/app.js`** holds all page logic. It builds every element with `createElement` and `textContent`.
- **`public/mapdata.js`** and **`public/usmap.js`** are the simplemaps US map (MIT license). `app.js` changes the map colors at run time. `mapdata.js` sets three options: no popups, no auto load, and a transparent background.
- **`public/select.js`** is the old simplemaps selection plug-in. The page no longer loads it. You can delete it.
- **`tools/check_site.py`** is the browser check. See "How to check the page".

## Page layout

The page has one URL and two views. Only one view shows at a time.

1. **Landing view** shows when the URL has no route. It has these parts, top to bottom:
   - The headline question "Which books have schools banned near you?".
   - A large ZIP code box and a "Find banned books" button.
   - A national summary: ban events, books, and states with recorded bans. It comes from `index.json`.
   - "Or pick a state": a state list (a `<select>`) and the map. The state list is the keyboard path to a state.
   - The map legend and a short note that counts show what the sources recorded.
2. **Results view** shows for a ZIP code or a state. It has these parts, top to bottom:
   - A search bar: a link back to all states, a ZIP box, and a state list.
   - A header: the place, one line about it, and one count cell per section.
   - A sticky section bar. It stays at the top of the screen while you scroll.
   - A filter box: search, school year, sort, and status chips.
   - For a state: the list of districts with the most banned books.
   - The book sections, each a grid of book cards.
   - For a ZIP code: the nearest libraries.
3. **Both views** end with the "Books Unbanned" section and the "About the data" footer. The page loads `books_unbanned.json` at start, so these always show.

The book details open in a native `<dialog>` element. It shows the cover, title, authors, every ban event, and links.

## Routes

The route is the part of the URL after `#`.

| URL | What the page shows |
|---|---|
| (no hash) | The landing view |
| `#zip=78217` | The results view for that ZIP code |
| `#state=TX` | The results view for that state |
| Anything else | The landing view. A bad ZIP such as `#zip=12` also shows the ZIP error message. |

- A new route adds a browser history entry, so the back button works.
- Opening a URL that has a hash shows that view.
- The `?data=folder/` query parameter still points the page at another data folder. The check script uses it with `tests/fixtures/site_data/`.
- The page loads these files, and no others:
  - On load: `index.json` and `books_unbanned.json`.
  - For `#state=XX`: `states/XX.json`.
  - For `#zip=NNNNN`: `zips/NNN.json`, then `states/XX.json` for each state of that ZIP code.
  - A state that `index.json` does not list shows "No bans recorded" without a request.

## The map

- The map shades each state by its number of ban events. The count comes from `index.json`.
- There are five shades and one neutral color. A state with no recorded bans gets the neutral color.

| Ban events | Color variable |
|---|---|
| 0 | `--map-none` |
| 1 to 9 | `--map-1` |
| 10 to 49 | `--map-2` |
| 50 to 199 | `--map-3` |
| 200 to 999 | `--map-4` |
| 1,000 or more | `--map-5` |

- The counts range from 1 (Louisiana) to 6,547 (Florida). A linear scale would give almost every state the lightest shade. So the ranges grow by about a factor of 4 to 5. The ranges are a guess, marked `TODO` in `MAP_STEPS`. Change them if the counts change a lot.
- Each color ramp is one hue from light to dark in the light theme. The dark theme flips it, so more bans means a lighter color on the dark background. The dataviz validator in ordinal mode passes both ramps.
- Each state has a text label. The code picks the light or dark label color that gives the higher contrast on that fill (`labelColorFor`). The lowest label contrast is 4.0 to 1 (dark theme, 10 to 49).
- A hover or a keyboard focus shows a tip with the state name and count. Each state is a tab stop with an `aria-label`. Enter or Space opens the state. The "Skip the map" link jumps past the 51 tab stops.
- The map shows the text "Simplemaps.com Trial". The map library draws it. A license from simplemaps removes it. The page does not hide it.

## Dark mode

- The dark theme applies only when the browser asks for it (`prefers-color-scheme: dark`). There is no switch.
- The dark theme defines every color again, including the `--map-*` colors and the cover placeholder colors.
- When the setting changes while the page is open, `initMap` repaints the map.
- Measured text contrast, all at least 4.5 to 1:

| Pair | Light | Dark |
|---|---|---|
| Main text on paper | 15.4 | 15.2 |
| Secondary text on paper | 8.6 | 10.6 |
| Caption text on paper | 5.7 | 7.0 |
| Accent text on paper | 5.7 | 7.1 |
| Banned badge | 6.3 | 7.1 |
| Pending badge | 6.8 | 8.8 |
| Restricted badge | 7.8 | 8.1 |

## Status badges

Each status has a name in text and a small shape. The shape repeats the status without color.

| Status in the data | Label | Shape |
|---|---|---|
| `banned` | Banned | Filled square |
| `banned_pending_investigation` | Banned pending investigation | Half-filled circle |
| `restricted` | Restricted | Empty square |

## Filter rules

The filter state is one object, `filters`. It has `q` (search text), `statuses` (a set), `year`, `sort`, and `district`. A new route resets it.

1. **Search** splits the text into words. A book matches when every word appears in its title or authors. The check ignores case and accents.
2. **Status chips** keep an event when its status is one of the selected chips. With no chip selected, every status passes.
3. **School year** keeps an event when its year equals the selected year. "Year not given" keeps events with no year.
4. **District** keeps an event when its district key equals the selected district. This filter exists only in the state view. A statewide event has no district, so a district filter removes statewide events.
5. The filters apply to every section at the same time. They apply to events first. Then the page groups the events that remain into one card per book.
6. A book shows when at least one of its events passes. The card then shows only the events that pass.
7. **Sort** orders the cards of each section:
   - "Title, A to Z" sorts by title.
   - "Newest school year" sorts by the latest year of the book's events, newest first. Ties sort by title.
   - "Most places that banned it" sorts by the number of districts, plus one for a statewide ban. Ties sort by title.
8. The counts in the header, the section bar, and each section title update with the filters. When a filter hides books, they read "N of M books". M is the count before filters.
9. The district list in the state view counts books per district with every filter except the district filter. Otherwise the click on a district would hide the other districts.
10. A section with no books before filters shows its own message, for example "No bans recorded in Orange County School District." A section that the filters empty shows "No books in this section match your filters" with a clear button.

## Cards

- A card shows the cover, title, authors, a status badge, and a line with the year. The badge shows the most serious status among the card's events (banned, then pending, then restricted).
- In a state view, and in "Rest of <state>", the line also shows the number of districts.
- A book with no cover, or with a cover that fails to load, shows a placeholder. The placeholder sets the title in type on a color. The color is one of 8, picked by `hashIndex` from the book ID. A book always gets the same color.
- Covers load lazily (`loading="lazy"`). A section draws 24 cards. "Show more" adds 24.
- Open Library sends a 1 by 1 pixel image for some missing covers. The page treats that as no cover.

## Book details

- The dialog lists every ban event of the book in the loaded state or states, not only the events of one section.
- Each event shows the place ("Statewide" or the district name), the school year, the status, and each source with its link. A source links to its own per-ban URL, else to the source page from `index.json`.
- An event with a `note` string shows "Note:" and the text.
- Links: "View on Open Library" shows only when `ol` is set. "Borrow a scan on Open Library" shows only when `scan` is true. "Find in WorldCat" always shows.
- `TODO` in `buildBookLinks`: the borrow link goes to the Open Library work page until state files carry an Internet Archive ID. With a field `ia` on the book, the link goes to `https://openlibrary.org/borrow/ia/<ia>`.

## Security rules in `app.js`

- Every element comes from `el()` or `createElement`. Text goes in with `textContent`. The code never puts file data into `innerHTML`.
- Every link from data goes through `safeUrl()`. It accepts only `http` and `https`.
- `readDataBase()` accepts only a plain relative path for `?data=`.

## Which function does what

### Helpers

| Function | Job |
|---|---|
| `readDataBase` | Reads `?data=` and returns the data folder. |
| `el` | Makes an element with class and text. |
| `safeUrl` | Returns an `http` or `https` URL, else `null`. |
| `makeLink` | Makes a link that opens in a new tab. |
| `fetchJson` | Loads one JSON file once and caches the promise. |
| `formatNumber`, `plural`, `formatDate`, `fold` | Format numbers, words, dates, and search text. |
| `cssVar` | Reads one CSS custom property. |

### Index data and landing page

| Function | Job |
|---|---|
| `indexState`, `banCount`, `knownStateName`, `stateName` | Read state names and counts. |
| `mapStates` | Lists the states of the map, sorted by name. |
| `renderIndexParts` | Fills the national summary and the "About the data" footer. |
| `fillStateSelect`, `wireStateSelect` | Fill a state list and send a choice to `navigate`. |
| `buildLegend` | Draws the map legend. |
| `loadUnbanned`, `buildProgramCard` | Draw the Books Unbanned cards. |

### Map

| Function | Job |
|---|---|
| `mapStep` | Gives the shade step (0 to 4, or -1 for none) of a ban count. |
| `applyMapColors` | Writes theme and state colors into the simplemaps settings. |
| `mixColors`, `luminance`, `contrastRatio`, `labelColorFor` | Color math for hover colors and label colors. |
| `initMap`, `ensureMap` | Start the map once, after `index.json` and while the landing view shows. |
| `showMapTip`, `positionMapTip`, `hideMapTip` | Draw the hover tip. |
| `drawOutline`, `highlightMapState` | Outline the focused state and the state of the current route. |
| `decorateMapStates` | Make each state a tab stop with a label and key handling. |

### Routes and loading

| Function | Job |
|---|---|
| `parseRoute` | Reads `location.hash` into `{kind, ...}`. |
| `navigate` | Changes the hash, or runs `route` when the hash stays the same. |
| `route` | Shows the view that the URL asks for. It runs on every `hashchange`. |
| `showHome` | Shows the landing view. |
| `showState`, `showZip` | Load files and show a results view or an error. |
| `prepareState` | Adds `_code` and `_book` to each ban event once per state file. |
| `loadState` | Loads and prepares one state file. |
| `isStale` | Tells a slow old request that a newer route has started. |

### Building a view (data only, no HTML)

| Function | Job |
|---|---|
| `buildStateView` | Makes the sections of a state: statewide, then district bans. |
| `buildEmptyStateView` | Makes the view of a state with no bans. |
| `buildZipView` | Makes one section per ZIP district, then statewide, then the rest of the state. |
| `sectionSpec` | Makes one section record. |
| `districtName` | Finds a district name in a state file. |

### Filters

| Function | Job |
|---|---|
| `defaultFilters`, `filtersActive` | Make and test the filter object. |
| `eventPasses`, `bookPasses`, `searchTokens` | Test one event and one book against the filters. |
| `makeGroups` | Filters events, groups them by book, and sorts the groups. |
| `baseCount` | Counts the books of a section before filters. |
| `applyFilters` | Recomputes every section and redraws all counts and cards. |
| `buildFilters`, `updateFilterUi`, `clearFilters` | Draw the filter box and keep it in step. |

### Drawing

| Function | Job |
|---|---|
| `renderSearchBar` | Draws the back link, the ZIP box, and the state list of the results view. |
| `renderLoading` | Draws the skeleton placeholders. |
| `renderNoData`, `renderLoadError` | Draw the "no data" panel and the "could not load" panel with a retry button. |
| `renderView` | Draws the whole results view. |
| `renderTally` | Draws one count cell for each section in the header. |
| `renderNav`, `markNav`, `observeSections`, `disconnectSpy` | Draw the section bar and mark the section on screen. |
| `buildSectionShell`, `renderSectionBody` | Draw one section and its cards, with "Show more". |
| `buildTopDistrictsShell`, `renderTopDistricts` | Draw the district ranking. |
| `buildCard`, `buildCover`, `buildPlaceholder`, `hashIndex` | Draw one book card and its cover. |
| `buildBadge`, `buildMark` | Draw a status badge. |
| `openBookDialog`, `eventsOfBook`, `buildEventItem`, `buildBookLinks`, `wireDialog` | Draw and control the book dialog. |
| `buildLibraries`, `buildLibraryCard` | Draw the nearest libraries with a "Directions" link. |

### Start-up

`start()` wires the dialog and the state list, loads Books Unbanned, runs `route()`, waits for `index.json`, fills the index parts, and starts the map. The ZIP forms and the in-page links use three listeners on `document`.

## How to check the page

Run this from the repo root:

```
.venv/Scripts/python.exe tools/check_site.py OUT_DIR 78217 84101 32792
```

- The script starts a local server and opens the page in headless Edge.
- It checks the files fetched on load and after a click on Texas, the three ZIP codes, the map shading, the book dialog, the filters, the error states, the `?data=` folder, the keyboard path, and the phone layout (390 px, no sideways scroll).
- It saves screenshots to `OUT_DIR` and prints a JSON report.
- It exits with code 1 when a check fails or when the page logs a console error or an HTTP error.
- Two tests cause errors on purpose: a ZIP code with no file (HTTP 404) and a blocked Texas file. The report lists those under `expected_404` and `expected_failed_fetch`. They do not count as errors.

## Known limits

- The filters are not in the URL. A shared link opens the place, not the filters.
- The national "books" number is the sum of the per-state numbers. A book banned in two states counts twice. `TODO` in `renderIndexParts`: use `totals.books` from `index.json` when the pipeline writes it.
- The map shows the simplemaps trial text. See "The map".
- Open Library covers come from a third party. A slow cover shows an empty frame until it loads.

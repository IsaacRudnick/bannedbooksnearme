"""Assembler: turns ban records from all sources into the site's data files.

Reads:  the source files in the registry (pipeline/sources.py), cache/ol_matches.csv,
        and cache/nces_districts.csv (through the openlibrary and districts modules).
Writes: data/books.csv, data/bans.csv, data/index.json, data/states/XX.json,
        review/low_confidence_books.csv, review/missing_authors.csv, review/counts.csv.
        (review/unmatched_districts.csv comes from the districts module.)

Steps, in order:
1. Collect ban records from every source and drop exact repeats within one source.
   Then fill empty authors from other records with the same main title (`fill_authors`).
2. Match books: give each record a book ID (Open Library work, or a hash).
3. Match districts: give each record a district key (LEAID, or "n:" plus the name).
4. Merge ban records into ban events. A district event is one book, one district, one
   school year. A statewide event is one book in one state, in any school year.
5. Write the tables, the JSON files, and the count report.
"""
import csv
import importlib
import json
from collections import Counter, defaultdict
from datetime import date

from pipeline.loaders import school_year_label
from pipeline.normalize import STATE_NAMES, author_surnames, book_id, book_key, fold, main_title, norm_title
from pipeline.sources import ROOT, SOURCES

# When two records for one event come from the same source, prefer the stronger status.
STATUS_RANK = {"banned": 0, "banned_pending_investigation": 1, "restricted": 2}

BOOK_COLUMNS = ["id", "title", "authors", "ol_work", "isbn", "cover_url", "scan"]
BAN_COLUMNS = ["book_id", "state", "district_name", "leaid", "school_year", "status", "scope", "sources", "source_urls", "note"]
LOW_COLUMNS = ["key", "source_title", "source_author", "work", "ol_title", "ol_authors",
               "title_score", "author_match", "ban_records"]
MISSING_AUTHOR_COLUMNS = ["source_id", "source_row", "title", "state", "district_name", "school_year"]
COUNT_COLUMNS = ["section", "name", "ban_records", "ban_events", "value"]

# The author fill takes an author from other states only when at least this share
# of the records for that title have the same first surname.
# TODO: tune after the owner reads review/missing_authors.csv and checks the filled authors.
AUTHOR_FILL_SHARE = 0.8
UNKNOWN_DISTRICT = {"key": "n:unknown", "name": "Unknown district", "leaid": ""}


class MissingModule(RuntimeError):
    """A pipeline module that another part of the project writes does not exist yet."""


class MissingSourceFile(RuntimeError):
    """A source file is not in sources/. The message says where to get it."""


def require(name):
    """Import pipeline.<name> or raise MissingModule with a clear message."""
    try:
        return importlib.import_module(f"pipeline.{name}")
    except ModuleNotFoundError as error:
        if error.name != f"pipeline.{name}":
            raise
        raise MissingModule(f"pipeline/{name}.py does not exist yet. This step needs it.") from error


# ---------------------------------------------------------------- 1. collect

def collect(root, stats, sources):
    """Load every source file. Return the ban records.

    If a source file is missing, raise MissingSourceFile. The message lists every missing file
    with its download page. The function never skips a source in silence, because a skipped
    source would change the site data without a warning.
    """
    missing = [source for source in sources if not (root / source.path).exists()]
    if missing:
        lines = ["Source files are missing. Git does not track them, because each publisher owns its data."]
        for source in missing:
            lines.append(f"  - Missing file: {source.path}")
            lines.append(f"    Download it from: {source.url}")
        lines.append('Read the README section "Get the source files" for the steps and the exact file names.')
        raise MissingSourceFile("\n".join(lines))
    records = []
    for source in sources:
        loaded = source.loader(root / source.path, source.id, stats=stats, **source.args)
        stats[f"records_loaded:{source.id}"] = len(loaded)
        print(f"Loaded {len(loaded):>6} ban records from {source.id}")
        records.extend(loaded)
    return records


def dedupe(records, stats):
    """Drop a record that repeats an earlier record of the same source in every field but row and URL."""
    seen, unique = set(), []
    for record in records:
        identity = tuple(v for k, v in record.items() if k not in ("source_row", "source_url"))
        if identity in seen:
            stats[f"duplicates_in_source:{record['source_id']}"] += 1
            continue
        seen.add(identity)
        unique.append(record)
    return unique


def pick_author(candidates, priority, share):
    """Return the author text of the most trusted candidate, or "" if no surname has enough support.

    `candidates` are records that have an author. The function reads the first surname of each
    author. It needs one surname that at least `share` of the candidates have.
    """
    surnames = [(author_surnames(c["author"]) or [""])[0] for c in candidates]
    counted = Counter(s for s in surnames if s)
    if not counted:
        return ""
    surname, count = counted.most_common(1)[0]
    if count < share * len(candidates):
        return ""
    supporters = [c for c, s in zip(candidates, surnames) if s == surname]
    return min(supporters, key=lambda c: priority[c["source_id"]])["author"]  # min keeps the first on a tie


def fill_authors(records, priority):
    """Fill the empty authors in place. Return (number filled, records still without an author).

    A record with no author takes the author of other records with the same main title
    (`norm_title(main_title(title))`). The function looks in the same state first. It needs all those
    records to share one first surname. If they do not, it looks in all states. It then needs a surname
    that at least AUTHOR_FILL_SHARE of the records share. It copies the author text of the most
    trusted record with that surname. Only records that had an author at the start give authors.
    """
    by_title = defaultdict(list)
    for record in records:
        if record["author"]:
            by_title[norm_title(main_title(record["title"]))].append(record)
    filled, missing = 0, []
    for record in records:
        if record["author"]:
            continue
        key = norm_title(main_title(record["title"]))
        candidates = by_title.get(key, []) if key else []
        same_state = [c for c in candidates if c["state"] == record["state"]]
        author = pick_author(same_state, priority, 1.0) or pick_author(candidates, priority, AUTHOR_FILL_SHARE)
        if author:
            record["author"] = author
            filled += 1
        else:
            missing.append(record)
    return filled, missing


def load_records(root, stats, sources):
    """Collect, dedupe, drop empty titles, and fill authors. Return the ban records.

    The extract step and the build step both call this function. So both see the same
    titles and authors, and the Open Library cache keys match.
    """
    priority = {s.id: s.priority for s in sources}
    records = dedupe(collect(root, stats, sources), stats)
    with_title = [r for r in records if r["title"]]
    stats["dropped_empty_title"] = len(records) - len(with_title)
    filled, missing = fill_authors(with_title, priority)
    stats["authors_filled"] = filled
    stats["authors_still_missing"] = len(missing)
    return dedupe(with_title, stats)  # A fill can turn two different records into repeats.


# ---------------------------------------------------------------- 2. books

def pick_best(records, priority):
    """Return {book key: the record from the most trusted source (first seen on ties)}."""
    best = {}
    for record in records:
        key = record["key"]
        if key not in best or priority[record["source_id"]] < priority[best[key]["source_id"]]:
            best[key] = record
    return best


def book_from_match(match, fallback_title):
    """Return a book dict built from a high-confidence Open Library match row."""
    cover = match.get("cover_id", "")
    return {
        "title": match.get("ol_title") or fallback_title,
        "authors": [a for a in match.get("ol_authors", "").split("|") if a],
        "ol": match["work"],
        "isbn": match.get("isbn") or None,
        "cover": f"https://covers.openlibrary.org/b/id/{cover}-M.jpg" if cover else None,
        "scan": bool(match.get("ia_id")),
    }


def book_from_record(record):
    """Return a book dict built from a source record (no Open Library match)."""
    authors = [record["author"]] if record["author"] else []
    return {"title": record["title"], "authors": authors, "ol": None, "isbn": None, "cover": None, "scan": False}


def match_books(records, matches, priority):
    """Give each record a "book" ID. Return (books by ID, low-confidence review rows).

    Only a "high" match uses the Open Library work ID. A "low" match goes to the review file.
    """
    best = pick_best(records, priority)
    uses = Counter(r["key"] for r in records)
    key_to_id, books, low_rows = {}, {}, []
    for key, record in best.items():
        match = matches.get(key) or {}
        if match.get("confidence") == "high" and match.get("work"):
            key_to_id[key] = match["work"]
            book = book_from_match(match, record["title"])
        else:
            key_to_id[key] = book_id("", key)
            book = book_from_record(record)
            if match.get("confidence") == "low":
                low_rows.append({
                    "key": key, "source_title": record["title"], "source_author": record["author"],
                    "work": match.get("work", ""), "ol_title": match.get("ol_title", ""),
                    "ol_authors": match.get("ol_authors", ""), "title_score": match.get("title_score", ""),
                    "author_match": match.get("author_match", ""), "ban_records": uses[key],
                })
        books.setdefault(key_to_id[key], book)
    for record in records:
        record["book"] = key_to_id[record["key"]]
    return books, low_rows


# ---------------------------------------------------------------- 3. districts

def district_info(pairs, matches):
    """Return {(state, raw name): {"key", "name", "leaid"}} for each pair."""
    info = {}
    for pair in pairs:
        name = pair[1]
        match = matches.get(pair) or {}
        if match.get("leaid"):
            info[pair] = {"key": match["leaid"], "name": match.get("nces_name") or name, "leaid": match["leaid"]}
        elif fold(name):
            info[pair] = {"key": "n:" + fold(name), "name": name, "leaid": ""}
        else:
            info[pair] = UNKNOWN_DISTRICT
    return info


def match_districts(records, matcher, root):
    """Give each record a "district" field: a dict with key, name, leaid (None for statewide).

    Return (record counts per (state, name) pair, district info per pair).
    """
    counts = Counter((r["state"], r["district_name"]) for r in records if r["scope"] == "district" and r["district_name"])
    matches = {}
    if counts:
        matches = matcher(list(counts), root / "cache" / "nces_districts.csv",
                          root / "review" / "unmatched_districts.csv", counts=dict(counts))
    info = district_info(counts, matches)
    for record in records:
        if record["scope"] == "statewide":
            record["district"] = None
        else:
            record["district"] = info.get((record["state"], record["district_name"]), UNKNOWN_DISTRICT)
    return counts, info


# ---------------------------------------------------------------- 4. events

def event_key(record, year):
    district = record["district"]["key"] if record["district"] else None
    return (record["book"], record["state"], record["scope"], district, year)


def candidate_years(calendar_year):
    """Return the two school years that contain a calendar year, the later one first.

    Example: 2024 is in 2024-25 (fall) and in 2023-24 (spring).
    """
    y = int(calendar_year)
    return [school_year_label(y), school_year_label(y - 1)]


def statewide_year(records, priority):
    """Return the school year of a statewide event: the year of its most trusted dated record.

    On a tie in trust, take the earliest school year. A record with no school year does not count.
    """
    dated = [r for r in records if r["school_year"]]
    if not dated:
        return ""
    best = min(priority[r["source_id"]] for r in dated)
    return min(r["school_year"] for r in dated if priority[r["source_id"]] == best)


def merge_events(records, priority):
    """Merge ban records into ban events. Return a list of {"key", "year", "records"} dicts.

    Statewide records merge on book and state only, whatever their school years.
    The event takes its year from `statewide_year`.
    A district record with a school year merges on the exact key. A district record without one
    joins an existing event for the same book and place whose school year holds
    its calendar year. If two such events exist, the later one wins.
    """
    events = {}
    undated = []
    for record in records:
        if record["scope"] == "statewide":
            events.setdefault(event_key(record, ""), []).append(record)
        elif record["school_year"]:
            events.setdefault(event_key(record, record["school_year"]), []).append(record)
        else:
            undated.append(record)
    # Undated records go second, so every dated event already exists when they look for one.
    for record in undated:
        year = ""
        if record["calendar_year"].isdigit():
            for candidate in candidate_years(record["calendar_year"]):
                if event_key(record, candidate) in events:
                    year = candidate
                    break
        events.setdefault(event_key(record, year), []).append(record)
    return [{"key": key, "records": group,
             "year": statewide_year(group, priority) if key[2] == "statewide" else key[4]}
            for key, group in events.items()]


def finish_event(event, priority):
    """Fill the status, note, sources, and place of an event from its records."""
    group = sorted(event["records"], key=lambda r: (priority[r["source_id"]], STATUS_RANK[r["status"]]))
    event["status"] = group[0]["status"]
    event["note"] = next((r["note"] for r in group if r["note"]), "")  # The note of the most trusted record that has one.
    urls = {}
    for record in group:  # sorted, so sources come out in priority order
        urls[record["source_id"]] = urls.get(record["source_id"]) or record["source_url"]
    event["sources"] = [{"id": sid, "url": url} if url else {"id": sid} for sid, url in urls.items()]
    event["book"], event["state"], event["scope"], _district, _year = event["key"]
    event["district"] = group[0]["district"]
    return event


# ---------------------------------------------------------------- 5. output

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8", newline="\n")


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def ban_json(event):
    """Return one ban as it appears in a state file."""
    ban = {"book": event["book"], "district": event["district"]["key"] if event["district"] else None,
           "year": event["year"], "status": event["status"], "scope": event["scope"], "sources": event["sources"]}
    if event["note"]:
        ban["note"] = event["note"]
    return ban


def state_files(events, books):
    """Return {state: state JSON dict}. Each file holds only the books and districts its bans use."""
    by_state = defaultdict(list)
    for event in events:
        by_state[event["state"]].append(event)
    files = {}
    for state, group in by_state.items():
        group.sort(key=lambda e: (books[e["book"]]["title"].lower(), e["book"],
                                  e["district"]["key"] if e["district"] else "", e["year"], e["scope"]))
        districts = {e["district"]["key"]: {"name": e["district"]["name"], "leaid": e["district"]["leaid"] or None}
                     for e in group if e["district"]}
        files[state] = {
            "state": state, "name": STATE_NAMES[state],
            "books": {b: books[b] for b in sorted({e["book"] for e in group})},
            "districts": districts, "bans": [ban_json(e) for e in group],
        }
    return files


def index_json(files, used_sources, sources, today):
    states = {s: {"name": f["name"], "bans": len(f["bans"]), "books": len(f["books"]),
                  "statewide": sum(1 for b in f["bans"] if b["scope"] == "statewide")}
              for s, f in sorted(files.items())}
    return {"generated": today, "states": states,
            "sources": {s.id: {"name": s.name, "url": s.url} for s in sources if s.id in used_sources}}


def table_rows(events, books):
    """Return (books.csv rows, bans.csv rows)."""
    used = sorted({e["book"] for e in events})
    book_rows = [{"id": b, "title": books[b]["title"], "authors": "|".join(books[b]["authors"]),
                  "ol_work": books[b]["ol"] or "", "isbn": books[b]["isbn"] or "",
                  "cover_url": books[b]["cover"] or "", "scan": "1" if books[b]["scan"] else "0"} for b in used]
    ban_rows = []
    for e in events:
        district = e["district"] or {"name": "", "leaid": ""}
        ban_rows.append({"book_id": e["book"], "state": e["state"], "district_name": district["name"],
                         "leaid": district["leaid"], "school_year": e["year"], "status": e["status"],
                         "scope": e["scope"], "sources": ";".join(s["id"] for s in e["sources"]),
                         "source_urls": ";".join(s.get("url", "") for s in e["sources"]), "note": e["note"]})
    ban_rows.sort(key=lambda r: (r["state"], r["district_name"], r["school_year"], r["book_id"]))
    return book_rows, ban_rows


def clean_state_dir(directory):
    """Remove old state files, so the folder matches this run."""
    if directory.exists():
        for old in directory.glob("*.json"):
            old.unlink()


# ---------------------------------------------------------------- report

def percent(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def count_report(records, events, stats, book_numbers, district_counts, info):
    """Return the rows of review/counts.csv.

    `book_numbers` is (unique keys, high-confidence keys, records with a high-confidence key).
    """
    rows = []

    def add(section, name, records_n="", events_n="", value=""):
        rows.append({"section": section, "name": name, "ban_records": records_n, "ban_events": events_n, "value": value})

    rec_by = {"source": Counter(r["source_id"] for r in records),
              "state": Counter(r["state"] for r in records),
              "school_year": Counter(r["school_year"] or "(none)" for r in records)}
    ev_by = {"source": Counter(s["id"] for e in events for s in e["sources"]),
             "state": Counter(e["state"] for e in events),
             "school_year": Counter(e["year"] or "(none)" for e in events)}
    for section in ("source", "state", "school_year"):
        for name in sorted(set(rec_by[section]) | set(ev_by[section])):
            add(section, name, rec_by[section][name], ev_by[section][name])
    for name, value in sorted(stats.items()):
        add("loader", name, value=value)
    keys, high, record_high = book_numbers
    add("summary", "ban_records_after_dedupe", value=len(records))
    add("summary", "ban_events", value=len(events))
    add("summary", "records_merged_into_other_events", value=len(records) - len(events))
    add("summary", "book_keys_total", value=keys)
    add("summary", "book_keys_high_confidence", value=high)
    add("summary", "book_match_rate_by_key", value=percent(high, keys))
    add("summary", "book_match_rate_by_record", value=percent(record_high, len(records)))
    matched = [p for p in district_counts if info[p]["leaid"]]
    add("summary", "district_names_total", value=len(district_counts))
    add("summary", "district_names_matched", value=len(matched))
    add("summary", "district_match_rate_by_name", value=percent(len(matched), len(district_counts)))
    weight = sum(district_counts[p] for p in matched)
    add("summary", "district_match_rate_by_record", value=percent(weight, sum(district_counts.values())))
    return rows


def print_report(rows):
    for row in rows:
        if row["section"] in ("source", "state", "school_year"):
            print(f"{row['section']:<12} {row['name']:<30} records {row['ban_records']:>6}  events {row['ban_events']:>6}")
        else:
            print(f"{row['section']:<8} {row['name']:<50} {row['value']}")


# ---------------------------------------------------------------- main entry

def copy_books_unbanned(root):
    """Copy the hand-checked Books Unbanned list from sources/ to data/ if it exists."""
    source = root / "sources" / "books_unbanned.json"
    if source.exists():
        write_json(root / "data" / "books_unbanned.json", json.loads(source.read_text(encoding="utf-8")))


def build(root=ROOT, matches=None, district_matcher=None, use_matching=True, sources=None, today=None, quiet=False):
    """Run the whole assemble step. Return the main results, for tests and checks.

    `matches`: dict of Open Library match rows. If None, read cache/ol_matches.csv.
    `district_matcher`: function like districts.match_districts. If None, use that one.
    `use_matching=False` skips both. Every book and district then stays unmatched.
    """
    sources = sources if sources is not None else SOURCES
    priority = {s.id: s.priority for s in sources}
    if not use_matching:
        matches, district_matcher = {}, (lambda pairs, *args, **kwargs: {})
    if matches is None:
        matches = require("openlibrary").load_matches(root / "cache" / "ol_matches.csv")
    if district_matcher is None:
        district_matcher = require("districts").match_districts

    stats = Counter()
    records = load_records(root, stats, sources)
    for record in records:
        record["key"] = book_key(record["title"], record["author"])

    books, low_rows = match_books(records, matches, priority)
    district_counts, info = match_districts(records, district_matcher, root)
    events = [finish_event(e, priority) for e in merge_events(records, priority)]

    files = state_files(events, books)
    book_rows, ban_rows = table_rows(events, books)
    clean_state_dir(root / "data" / "states")
    for state, content in files.items():
        write_json(root / "data" / "states" / f"{state}.json", content)
    used_sources = {s["id"] for e in events for s in e["sources"]}
    write_json(root / "data" / "index.json", index_json(files, used_sources, sources, today or date.today().isoformat()))
    # The two canonical tables. They sit in data/ so that site visitors can download them too.
    write_csv(root / "data" / "books.csv", BOOK_COLUMNS, book_rows)
    write_csv(root / "data" / "bans.csv", BAN_COLUMNS, ban_rows)
    copy_books_unbanned(root)
    write_csv(root / "review" / "low_confidence_books.csv", LOW_COLUMNS, low_rows)
    write_csv(root / "review" / "missing_authors.csv", MISSING_AUTHOR_COLUMNS, [r for r in records if not r["author"]])

    keys = {r["key"] for r in records}
    high = {k for k in keys if (matches.get(k) or {}).get("confidence") == "high" and matches[k].get("work")}
    numbers = (len(keys), len(high), sum(1 for r in records if r["key"] in high))
    rows = count_report(records, events, stats, numbers, district_counts, info)
    write_csv(root / "review" / "counts.csv", COUNT_COLUMNS, rows)
    if not quiet:
        print_report(rows)
    return {"records": records, "events": events, "books": books, "files": files, "stats": stats, "count_rows": rows}

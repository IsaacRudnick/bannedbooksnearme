"""Match banned-book records (title + author) to Open Library works.

Why the dumps and not the API: Open Library asks bulk users not to call its
API. The site has about 8,400 unique title and author pairs. So this module
never makes a network call. It reads the monthly dump files in
raw/openlibrary/ instead. The result goes to cache/ol_matches.csv, and git
tracks that small file. With it, a person can rebuild the site without the
dumps.

Stages (each one streams a gzip file once and never loads it into memory):

1. Works dump. Find every work whose normalized title (or main title) equals
   a needed title. These works are the candidates.
2. Authors dump. Look up the names of the candidates' authors.
3. Editions dump. For each candidate, count its editions and pick one ISBN,
   one cover, and one Internet Archive ID.
4. Score. Compare each source pair with its candidates and keep the best one.
5. Write. Add one row per new key to the cache file, sorted by key.

Every key that is missing from the cache gets a row, even when it has no
candidate (confidence "none"). So the next run skips it.

Command line:  python -m pipeline.openlibrary --from-sources
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rapidfuzz import fuzz

from pipeline.normalize import author_surnames, book_key, fold, main_title, norm_title  # main_title moved to normalize.py. Importing it here keeps ol.main_title working.

COLUMNS = ["key", "work", "ol_title", "ol_authors", "title_score",
           "author_match", "confidence", "isbn", "cover_id", "ia_id"]

# TODO: tune after reviewing review/low_confidence_books.csv (the owner reads
# the low rows and says if the cut-off is too strict or too loose).
HIGH_TITLE_SCORE = 90

# TODO: decide after reviewing review/low_confidence_books.csv. True also compares the
# source's main title (before ':') with the work title. This raises the high count but
# can link a series book to the first book of the series.
COMPARE_SOURCE_MAIN_TITLE = True

# TODO: tune after reviewing the high rows that only match by spelling (they have
# author_match 1 but a different spelling). 80 allows one wrong letter in a 6-letter name (ratio 83).
AUTHOR_FUZZY_SCORE = 80
AUTHOR_FUZZY_MIN_LETTERS = 6

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}  # same list as normalize.py

DEFAULT_RAW_DIR = Path("raw/openlibrary")
DEFAULT_CACHE = Path("cache/ol_matches.csv")

SOURCES = [  # (csv path, title column, author column)
    (Path("sources/pen_2023-24_index.csv"), "Title", "Author"),
    (Path("sources/magnusson_2025-08.csv"), "New_Book_Title", "New_Book_Author"),
]


# ---------------------------------------------------------------- the cache

def load_matches(cache_path=DEFAULT_CACHE):
    """Return the cache as a dict keyed by `key`. Return {} if the file is missing."""
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return {}
    with open(cache_path, newline="", encoding="utf-8") as f:
        return {row["key"]: row for row in csv.DictReader(f)}


def write_matches(rows, cache_path):
    """Write all rows to the cache file, sorted by key so git diffs stay small."""
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow({c: rows[key].get(c, "") for c in COLUMNS})


# ------------------------------------------------------- what we need to find

def collect_needs(pairs):
    """Group raw (title, author) pairs by book key.

    Return {key: {"title": normalized title, "main": normalized main title,
    "surnames": set of surnames from every pair with this key}}.
    The surnames are a set because two spellings of one author can share a key.
    """
    needs = {}
    for title, author in pairs:
        key = book_key(title, author)
        need = needs.setdefault(key, {
            "title": norm_title(title),
            "main": norm_title(main_title(title)),
            "surnames": set(),
        })
        need["surnames"].update(author_surnames(author))
    return needs


def wanted_titles(needs):
    """Return the set of normalized titles to look for in the works dump."""
    titles = set()
    for need in needs.values():
        titles.update(t for t in (need["title"], need["main"]) if t)
    return titles


# --------------------------------------------------------- reading the dumps

def _gzip_program():
    """Find a gzip program. It is much faster than Python's gzip module."""
    found = shutil.which("gzip")
    if found:
        return found
    git_gzip = Path("C:/Program Files/Git/usr/bin/gzip.exe")
    return str(git_gzip) if git_gzip.exists() else None


class IncompleteDump(Exception):
    """A dump file is cut off, damaged, or missing."""


def _read_chunks(path):
    """Yield bytes chunks of the decompressed file. Raise IncompleteDump if it ends early."""
    if not Path(path).exists():
        raise IncompleteDump(f"{path} is missing")
    program = _gzip_program()
    if program:
        proc = subprocess.Popen([program, "-dc", str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while chunk := proc.stdout.read(1 << 24):
            yield chunk
        error = proc.stderr.read().decode(errors="replace").strip()
        proc.wait()
        if proc.returncode:
            raise IncompleteDump(f"{path} is cut off or damaged ({error})")
        return
    import gzip
    try:
        with gzip.open(path, "rb") as f:
            while chunk := f.read(1 << 24):
                yield chunk
    except EOFError:
        raise IncompleteDump(f"{path} is cut off") from None


def iter_lines(path, allow_partial=False):
    """Yield each line of a gzip dump as bytes, without the newline.

    A cut-off or missing file raises IncompleteDump. With allow_partial the
    function prints a warning and stops instead. Use that only to develop,
    because a partial dump gives wrong "none" results.
    """
    rest = b""
    try:
        for chunk in _read_chunks(path):
            lines = (rest + chunk).split(b"\n")
            rest = lines.pop()
            yield from lines
    except IncompleteDump as error:
        if not allow_partial:
            raise
        print(f"  warning: {error}")
    if rest:
        yield rest


def _decode_json_string(raw):
    """Turn the raw bytes between JSON quotes into text, resolving \\u escapes."""
    text = raw.decode("utf-8", errors="replace")
    if "\\" in text:
        try:
            return json.loads(f'"{text}"')
        except ValueError:
            pass
    return text


def _work_number(work_id):
    """Return 8034526 for 'OL8034526W'. Used as the last tie-break."""
    return int(re.sub(r"\D", "", work_id) or 0)


# ------------------------------------------------- stage 1: the works dump

_TITLE_RE = re.compile(rb'"title": "((?:[^"\\]|\\.)*)"')
_SUBTITLE_RE = re.compile(rb'"subtitle": "((?:[^"\\]|\\.)*)"')


def title_forms(title, subtitle=""):
    """Return the normalized forms of a work title that can match a source title."""
    forms = {norm_title(title)}
    main = main_title(title)
    if main != title:  # folding is the slow part, so skip it when the two are equal
        forms.add(norm_title(main))
    if subtitle:
        forms.add(norm_title(f"{title} {subtitle}"))
    forms.discard("")
    return forms


def _maybe_wanted(line, wanted):
    """Cheap test on the raw line. It may say yes for a non-match, never no for a match.

    It avoids json.loads, which is the slow part, for nearly every line.
    """
    titles = [_decode_json_string(m) for m in _TITLE_RE.findall(line)]
    if not titles:
        return False
    subtitles = []
    if b'"subtitle"' in line:
        subtitles = [_decode_json_string(m) for m in _SUBTITLE_RE.findall(line)]
    for title in titles:
        if title_forms(title) & wanted:
            return True
        for subtitle in subtitles:
            if title_forms(title, subtitle) & wanted:
                return True
    return False


def _author_keys(work):
    """Return the author IDs (like 'OL92934A') listed on a work record."""
    ids = []
    for entry in work.get("authors") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("author", entry)
        key = ref.get("key", "") if isinstance(ref, dict) else ""
        if key.startswith("/authors/"):
            ids.append(key.rsplit("/", 1)[1])
    return ids


def _first_cover(record):
    """Return the first real cover ID of a work or edition as text, or "". IDs <= 0 are placeholders."""
    covers = [c for c in record.get("covers") or [] if isinstance(c, int) and c > 0]
    return str(covers[0]) if covers else ""


def new_candidate(work_id, title, subtitle, author_ids, cover_id=""):
    """Return a fresh candidate record. Stage 3 fills in editions, isbn, and ia_id."""
    return {"work": work_id, "title": title, "subtitle": subtitle,
            "forms": title_forms(title, subtitle), "author_ids": author_ids,
            "cover_id": cover_id, "editions": 0, "isbn": "", "ia_id": "",
            "_isbn_rank": (-1, -1)}


def scan_works(lines, wanted):
    """Stage 1. Read dump lines. Return {work_id: candidate} for works with a wanted title form."""
    candidates = {}
    for line in lines:
        if not _maybe_wanted(line, wanted):
            continue
        parts = line.split(b"\t", 4)
        if len(parts) < 5:
            continue
        try:
            work = json.loads(parts[4])
        except ValueError:
            continue
        title = work.get("title") or ""
        subtitle = work.get("subtitle") or ""
        forms = title_forms(title, subtitle)
        if not (forms & wanted):
            continue
        work_id = parts[1].decode().rsplit("/", 1)[1]
        cover_id = _first_cover(work)
        candidates[work_id] = new_candidate(work_id, title, subtitle, _author_keys(work), cover_id)
    return candidates


# ----------------------------------------------- stage 2: the authors dump

def scan_authors(lines, author_ids):
    """Stage 2. Return {author_id: name} for the author IDs we need."""
    names = {}
    wanted = {i.encode() for i in author_ids}
    for line in lines:
        parts = line.split(b"\t", 2)
        if len(parts) < 3 or parts[1].rsplit(b"/", 1)[-1] not in wanted:
            continue
        try:
            record = json.loads(line.split(b"\t", 4)[4])
        except (ValueError, IndexError):
            continue
        name = record.get("name") or record.get("personal_name") or ""
        names[parts[1].decode().rsplit("/", 1)[1]] = name
    return names


# ----------------------------------------------- stage 3: the editions dump

_WORK_REF_RE = re.compile(rb"/works/(OL\d+W)")


def _edition_isbn(edition):
    """Return (isbn text, 1 if it is an ISBN-13 else 0), or ("", -1) if none."""
    for field, rank in (("isbn_13", 1), ("isbn_10", 0)):
        values = edition.get(field) or []
        if values:
            return str(values[0]), rank
    return "", -1


def add_edition(candidate, edition):
    """Fold one edition record into its candidate work's totals."""
    candidate["editions"] += 1
    cover = _first_cover(edition)
    if cover and not candidate["cover_id"]:
        candidate["cover_id"] = cover
    ocaid = edition.get("ocaid")
    if ocaid and not candidate["ia_id"]:
        candidate["ia_id"] = ocaid
    isbn, is13 = _edition_isbn(edition)
    # An edition with a cover wins first. Among those, an ISBN-13 wins.
    rank = (1 if cover else 0, is13)
    if isbn and rank > candidate["_isbn_rank"]:
        candidate["isbn"] = isbn
        candidate["_isbn_rank"] = rank


def scan_editions(lines, candidates):
    """Stage 3. Add edition totals to each candidate. Return nothing."""
    for line in lines:
        refs = {m.decode() for m in _WORK_REF_RE.findall(line)}
        hits = refs & candidates.keys()
        if not hits:
            continue
        try:
            edition = json.loads(line.split(b"\t", 4)[4])
        except (ValueError, IndexError):
            continue
        for work_id in hits:
            add_edition(candidates[work_id], edition)


# ----------------------------------------------------------- stage 4: score

def title_score(need, candidate):
    """Return the best rapidfuzz ratio (0 to 100) between source title and work title.

    The source title is compared with every form of the work title. When the source
    title has a subtitle ("Mediocre: The Dangerous Legacy ..."), its main title is also
    compared with the work's full title, because Open Library often drops the subtitle.
    The main title is not compared with the work's main title, so "Dune: Messiah" does
    not score 100 against "Dune: Children of Dune".
    """
    score = max((fuzz.ratio(need["title"], f) for f in candidate["forms"]), default=0)
    if COMPARE_SOURCE_MAIN_TITLE and need["main"] and need["main"] != need["title"]:
        full = {norm_title(candidate["title"])}
        if candidate["subtitle"]:
            full.add(norm_title(f"{candidate['title']} {candidate['subtitle']}"))
        score = max(score, *(fuzz.ratio(need["main"], f) for f in full))
    return score


def _surname_in_names(surname, names):
    """Return True if the surname is a whole-word phrase in a name, or nearly so.

    "Nearly" covers source typos such as "Guvatt" for "Guyatt". It applies only to
    one-word surnames of 6 or more letters, so short names like "Lee" and "Li" stay exact.
    """
    if any(f" {surname} " in n for n in names):
        return True
    if " " in surname or len(surname) < AUTHOR_FUZZY_MIN_LETTERS:
        return False
    words = {w for n in names for w in n.split()}
    return any(fuzz.ratio(surname, w) >= AUTHOR_FUZZY_SCORE for w in words)


def author_matches(surnames, author_names):
    """Return 1 if a source surname appears in any work author name, else 0.

    A surname can have several words ("cavell clarke"), so the test pads both sides
    with spaces and looks for the phrase. A trailing "jr" or "sr" is dropped first,
    because normalize.author_surnames keeps it in the "Vonnegut Jr., Kurt" form.
    """
    names = [f" {fold(n)} " for n in author_names]
    for surname in surnames:
        words = [w for w in surname.split() if w not in SUFFIXES]
        if words and _surname_in_names(" ".join(words), names):
            return 1
    return 0


def score_candidate(need, candidate, author_names):
    """Return a copy of the candidate with title_score, author_match, confidence added."""
    score = title_score(need, candidate)
    match = author_matches(need["surnames"], author_names)
    high = score >= HIGH_TITLE_SCORE and match == 1
    return {**candidate, "author_names": author_names, "title_score": round(score),
            "author_match": match, "confidence": "high" if high else "low"}


def preference(scored):
    """Sort key: smaller is better. High first, then score, editions, cover, low OL number."""
    return (scored["confidence"] != "high", -scored["title_score"], -scored["editions"],
            not scored["cover_id"], _work_number(scored["work"]))


def best_match(need, candidates, names):
    """Return the best scored candidate for one key, or None if there is none."""
    scored = [score_candidate(need, c, [names[a] for a in c["author_ids"] if a in names])
              for c in candidates]
    return min(scored, key=preference) if scored else None


def to_row(key, best):
    """Return the cache row for a key. `best` None means no candidate."""
    if best is None:
        return {"key": key, "confidence": "none"}
    return {"key": key, "work": best["work"], "ol_title": best["title"],
            "ol_authors": "|".join(best["author_names"]),
            "title_score": best["title_score"], "author_match": best["author_match"],
            "confidence": best["confidence"], "isbn": best["isbn"],
            "cover_id": best["cover_id"], "ia_id": best["ia_id"]}


def build_rows(needs, candidates, names):
    """Return {key: cache row} for every key in `needs`."""
    by_form = {}
    for c in candidates.values():
        for form in c["forms"]:
            by_form.setdefault(form, []).append(c)
    rows = {}
    for key, need in needs.items():
        found = {}
        for title in (need["title"], need["main"]):
            for c in by_form.get(title, []):
                found[c["work"]] = c
        rows[key] = to_row(key, best_match(need, list(found.values()), names))
    return rows


# --------------------------------------------------------------- the driver

def _timed(label, func):
    """Run func, print how long it took, return its result."""
    start = time.time()
    result = func()
    print(f"  {label}: {time.time() - start:.0f} s")
    return result


def extract(pairs, raw_dir=DEFAULT_RAW_DIR, cache_path=DEFAULT_CACHE, allow_partial=False):
    """Match the (title, author) pairs. Add rows for keys not in the cache yet.

    If every key is cached, the dumps are not touched.
    `allow_partial` lets a cut-off or missing dump pass. Use it only to develop,
    and point cache_path at a scratch file, because a partial dump gives wrong "none" rows.
    Return a dict with the summary counts.
    """
    raw_dir, cache_path = Path(raw_dir), Path(cache_path)
    needs = collect_needs(pairs)
    cache = load_matches(cache_path)
    missing = {k: v for k, v in needs.items() if k not in cache}
    summary = {"requested": len(needs), "cached": len(needs) - len(missing),
               "high": 0, "low": 0, "none": 0}
    if missing:
        rows = _match_from_dumps(missing, raw_dir, allow_partial)
        for row in rows.values():
            summary[row["confidence"]] += 1
        cache.update(rows)
        write_matches(cache, cache_path)
    print(f"Open Library match: {summary['requested']} keys requested, "
          f"{summary['cached']} already cached, new: {summary['high']} high, "
          f"{summary['low']} low, {summary['none']} none.")
    return summary


def _match_from_dumps(needs, raw_dir, allow_partial=False):
    """Run stages 1 to 4 on the dumps for the given needs. Return the new rows."""
    def lines(name):
        return iter_lines(raw_dir / f"ol_dump_{name}_latest.txt.gz", allow_partial)

    wanted = wanted_titles(needs)
    print(f"Reading dumps for {len(needs)} keys, {len(wanted)} wanted titles.")
    candidates = _timed("works pass", lambda: scan_works(lines("works"), wanted))
    author_ids = {a for c in candidates.values() for a in c["author_ids"]}
    print(f"  {len(candidates)} candidate works, {len(author_ids)} authors.")
    names = _timed("authors pass", lambda: scan_authors(lines("authors"), author_ids))
    _timed("editions pass", lambda: scan_editions(lines("editions"), candidates))
    return build_rows(needs, candidates, names)


def pairs_from_sources():
    """Read (title, author) pairs from the two source CSV files. No loaders needed."""
    for path, title_col, author_col in SOURCES:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if (row.get(title_col) or "").strip():
                    yield row[title_col], row.get(author_col) or ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from-sources", action="store_true",
                        help="read pairs from the PEN and Magnusson source files")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--allow-partial", action="store_true",
                        help="develop only: accept cut-off or missing dumps (use a scratch --cache)")
    args = parser.parse_args(argv)
    if not args.from_sources:
        parser.error("use --from-sources")
    extract(pairs_from_sources(), args.raw_dir, args.cache, args.allow_partial)


if __name__ == "__main__":
    sys.exit(main())

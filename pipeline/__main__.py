"""Command line entry point: `python -m pipeline [extract|build|all]`.

extract: read raw/ and write the small cache files (Open Library matches, geography).
build:   read sources/ and cache/, then write build/, data/, and review/.
all:     extract, then build. This is the default.

Run it from the repo root. The extract step can take a long time on the first run.
"""
import argparse
import sys
from collections import Counter

from pipeline.assemble import ROOT, MissingModule, MissingSourceFile, build, load_records, require
from pipeline.sources import SOURCES


def book_pairs():
    """Return the unique (title, author) pairs of every ban record."""
    records = load_records(ROOT, Counter(), SOURCES)
    return sorted({(r["title"], r["author"]) for r in records if r["title"]})


def run_extract():
    pairs = book_pairs()
    print(f"Extract: {len(pairs)} unique title and author pairs.")
    require("openlibrary").extract(pairs, ROOT / "raw" / "openlibrary", ROOT / "cache" / "ol_matches.csv")
    require("geo").extract(ROOT / "raw", ROOT / "cache")


def run_build(use_matching):
    build(ROOT, use_matching=use_matching)
    if use_matching:
        require("geo").build_zip_files(ROOT / "cache", ROOT / "data" / "zips")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__.split("\n")[0])
    parser.add_argument("step", nargs="?", default="all", choices=["extract", "build", "all"])
    parser.add_argument("--no-match", action="store_true",
                        help="build without Open Library or district matching, and skip the ZIP files (for a quick check)")
    args = parser.parse_args(argv)
    try:
        if args.step in ("extract", "all"):
            run_extract()
        if args.step in ("build", "all"):
            run_build(use_matching=not args.no_match)
    except (MissingModule, MissingSourceFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

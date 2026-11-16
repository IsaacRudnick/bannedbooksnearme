Small gzip dumps for tests/test_openlibrary.py. All lines are real lines from the
Open Library monthly dump (2026-11), except the filler lines described below.

ol_dump_works_latest.txt.gz
  Eight work lines: Call Me by Your Name (by Aciman, and by others), Gender Queer,
  The Perks of Being a Wallflower, The Haters. Plus two shortened lines from the
  head of the dump and one made-up redirect line, so the scanner has lines to skip.
ol_dump_authors_latest.txt.gz
  The author lines for those works, plus one made-up unrelated author.
ol_dump_editions_latest.txt.gz
  All 94 edition lines in the dump that point to those eight works.

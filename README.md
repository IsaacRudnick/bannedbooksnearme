# Banned Books Near Me

Using data from the [PEN America index of school book bans](https://pen.org/book-bans/pen-america-index-of-school-book-bans-2024-2025/)
and the [Book Censorship Database by Dr. Tasslyn Magnusson](https://www.everylibraryinstitute.org/book_censorship_database_magnusson),
this site lists banned books in a state and provides access to the books via [Open Library](https://openlibrary.org/) and [WorldCat](https://www.worldcat.org/).

The data is combined into a standardized format, each book is looked up on Open Library and WorldCat, and the results are cached in a SQLite database. The data that the site needs is then provided in a JSON file requested on the client side to allow static serving from GitHub Pages.

## Process to get JSON

```bash
pip install -r requirements.txt
python ./combine_sources.py
python ./filter_for_cover.py
python ./books_to_json.py
```

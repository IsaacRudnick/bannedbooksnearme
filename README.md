# Banned Books Near Me

Using data from the [PEN America index of school book bans](https://pen.org/book-bans/pen-america-index-of-school-book-bans-2024-2025/)
and the [Book Censorship Database by Dr. Tasslyn Magnusson](https://www.everylibraryinstitute.org/book_censorship_database_magnusson),
this site lists banned books in a state and provides access to the books via [Open Library](https://openlibrary.org/) and [WorldCat](https://www.worldcat.org/).

## Run it myself

To run this, you need Python 3. I'm using Python 3.10.12. 

```bash
pip install -r requirements.txt
```

Then, we need to standardize the data. SourceA is from PEN America and SourceB is from Dr. Magnusson.
You can skip this step; the `combined_books.csv` file is already provided.

```bash
python combine_sources.py
python filter_for_cover.py
```

Finally, run the Flask app:

```bash
python server.py
```

This runs on port 8001. You can change this as desired.

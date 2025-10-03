# preprocess_books.py
import csv
import json


def worldcat_url(title, author):
    query = f"{title} {author}".replace(" ", "+")
    return f"https://www.worldcat.org/search?q={query}"


books = []
with open("combined_books.csv", newline="", encoding="utf-8") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        if row["cover_url"]:  # only keep books with covers
            row["worldcat"] = worldcat_url(row["title"], row["author"])

            # Source mapping
            row["source"] = {
                "A": "PEN America index of school book bans",
                "B": "Book Censorship Database by Dr. Tasslyn Magnusson"
            }.get(row["source"], row["source"])

            # Availability mapping
            row["availability"] = {
                "private": "Private",
                "borrow_available": "Can be borrowed online",
                "Unknown": "Check WorldCat",
                "open": "Available to read online"
            }.get(row["availability"], "Unknown")

            if not row["district"]:
                row["district"] = "Unknown"

            books.append(row)

with open("books.json", "w", encoding="utf-8") as f:
    json.dump(books, f, ensure_ascii=False, indent=2)

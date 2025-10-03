import csv
import time
import requests_cache
from progress.bar import Bar
from requests.utils import requote_uri

# --- State abbreviations ---
ABBREVIATIONS = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
    'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
    'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
    'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
    'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
    'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
    'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
    'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
    'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
    'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
    'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT',
    'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
    'Wisconsin': 'WI', 'Wyoming': 'WY'
}

# --- Setup cached session with throttle hook ---


def make_throttle_hook(timeout=0.5):
    """Hook to add delay only for non-cached requests"""
    def hook(response, *args, **kwargs):
        if not getattr(response, 'from_cache', False):
            time.sleep(timeout)
        return response
    return hook


session = requests_cache.CachedSession('book_cache', expire_after=86400 * 30)  # 30 day cache
session.hooks['response'].append(make_throttle_hook(0.1))

# --- Book lookup (synchronous) ---


def lookup_book(title, author, state):
    result = {
        "title": title,
        "author": author,
        "state": state,
        "openLibraryKey": None,
        "isbn": None,
        "availability": "Unknown"
    }

    def fetch_json(url):
        try:
            resp = session.get(url, timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f"Error fetching {url}: {e}")
        return None

    # Step 1: Query Open Library API
    query = f"?title={title}&author={author}&limit=1&fields=*,availability"
    url = f"https://openlibrary.org/search.json{requote_uri(query)}"
    data = fetch_json(url)

    if data and data.get("numFound", 0) == 0:
        # Retry more generic query
        query = f"?q={title}&limit=1&fields=*,availability"
        url = f"https://openlibrary.org/search.json{requote_uri(query)}"
        data = fetch_json(url)

    if data and len(data.get("docs", [])) > 0:
        doc = data["docs"][0]
        result["openLibraryKey"] = doc.get("key")
        result["isbn"] = doc.get("isbn", [None])[0]
        availability = doc.get("availability")
        if isinstance(availability, dict):
            result["availability"] = availability.get("status", "Unknown")
        else:
            result["availability"] = "Unknown"
    else:
        print(f"\nNo data found for {title} by {author}")
        return None

    return result

# --- Load CSV sources ---


def load_sourceA(filename):
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            {
                "title": row["Title"],
                "author": row["Author"],
                "state": ABBREVIATIONS.get(row["State"], row["State"]),
                "source": "A",
                "district": row.get("Overseeing_Agency", "")
            }
            for row in reader
        ]


def load_sourceB(filename):
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            {
                "title": row["New_Book_Title"],
                "author": row["New_Book_Author"],
                "state": row["State"],
                "source": "B",
                "district": row.get("County", "")
            }
            for row in reader if row["Decision"] == "Banned/Removed"
        ]

# --- Main ---


def main():
    all_books = load_sourceA("./sources/sourceA.csv") + load_sourceB("./sources/sourceB.csv")
    bar = Bar('Processing', max=len(all_books), suffix='%(index)d/%(max)d - ETA: %(eta)ds')

    # Open CSV for writing immediately
    with open("combined_books.csv", "w", newline="", encoding="utf-8") as f:
        fieldnames = ["title", "author", "state", "source", "district", "openLibraryKey", "isbn", "availability"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for book in all_books:
            res = lookup_book(book["title"], book["author"], book["state"])
            if res:
                res["source"] = book["source"]
                res["district"] = book["district"]
                writer.writerow(res)
            bar.next()
    bar.finish()
    print("✅ Combined CSV written to combined_books.csv")


if __name__ == "__main__":
    main()

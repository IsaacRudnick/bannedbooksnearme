import requests_cache
import csv
import time


def cover_url(row):
    if row.get("isbn"):
        return f"https://covers.openlibrary.org/b/isbn/{row['isbn']}-M.jpg?default=false"
    elif row.get("openLibraryKey"):
        id = row["openLibraryKey"].replace("/works/", "")
        return f"https://covers.openlibrary.org/b/olid/{id}-M.jpg?default=false"
    return None


def make_throttle_hook(timeout=0.5):
    """Hook to add delay only for non-cached requests"""
    def hook(response, *args, **kwargs):
        if not getattr(response, 'from_cache', False):
            time.sleep(timeout)
        return response
    return hook


session = requests_cache.CachedSession('book_cache', expire_after=86400 * 30)  # 30 day cache
session.hooks['response'].append(make_throttle_hook(0.1))

# Read CSV
with open("combined_books.csv", newline="", encoding="utf-8") as csvfile:
    reader = csv.DictReader(csvfile)
    rows = list(reader)
    print(f"Loaded {len(rows)} rows from combined_books.csv")

    for i, row in enumerate(rows):
        url = cover_url(row)

        if url is None:
            row["cover_url"] = ""
            print(f"[{i+1}/{len(rows)}] No cover URL available for '{row.get('title')}' by {row.get('author')}")
            continue

        try:
            resp = session.get(url, timeout=5)
            if resp.status_code == 200:
                row["cover_url"] = url
                print(f"[{i+1}/{len(rows)}] Valid cover URL for '{row.get('title')}' by {row.get('author')}: {url}")
            else:
                row["cover_url"] = ""
                print(
                    f"[{i+1}/{len(rows)}] No valid cover URL for '{row.get('title')}' by {row.get('author')} (Status: {resp.status_code})")
        except Exception as e:
            row["cover_url"] = ""
            print(f"[{i+1}/{len(rows)}] Error fetching cover URL for '{row.get('title')}' by {row.get('author')}: {e}")

        # Make sure cover_url is in the header
    fieldnames = list(reader.fieldnames) if reader.fieldnames else []
    if "cover_url" not in fieldnames:
        fieldnames.append("cover_url")

# Write back to CSV
with open("combined_books.csv", "w", newline="", encoding="utf-8") as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print("Updated combined_books.csv with cover_url column.")

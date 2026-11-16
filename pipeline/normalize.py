"""Shared text normalization. Every module uses these functions to make keys.

Change these functions with care: the keys in cache/ol_matches.csv depend on them.
"""
import hashlib
import re
import unicodedata

STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}
STATE_NAMES = {code: name for name, code in STATES.items()}


def state_code(value):
    """Return the two-letter code for a state name or code. Return "" if unknown."""
    value = (value or "").strip()
    if value.upper() in STATE_NAMES:
        return value.upper()
    return STATES.get(value.title(), STATES.get(value, ""))


def fold(text):
    """Lowercase, remove accents, turn '&' into 'and', drop punctuation, collapse spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[’'`]", "", text)          # "don't" -> "dont"
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


_LEADING_ARTICLE = re.compile(r"^(the|a|an) ")


def norm_title(title):
    """Fold a title and drop one leading article."""
    return _LEADING_ARTICLE.sub("", fold(title))


_AUTHOR_SPLIT = re.compile(r";|\band\b|&|/|\bwith\b", re.IGNORECASE)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}


def author_surnames(author):
    """Return the folded surnames in an author field, in source order.

    Handles "Last, First", "First Last", and several authors joined by ";", "and", or "&".
    """
    surnames = []
    for part in _AUTHOR_SPLIT.split(author or ""):
        part = part.strip()
        if not part:
            continue
        if "," in part:
            name = fold(part.split(",")[0])
        else:
            words = [w for w in fold(part).split() if w not in _SUFFIXES]
            name = words[-1] if words else ""
        if name and name not in surnames:
            surnames.append(name)
    return surnames


def main_title(title):
    """Return the text before ':' or ' - ' or '(' (the title without a subtitle or series note)."""
    return re.split(r":| - |\(", title, maxsplit=1)[0]


def book_key(title, author):
    """Return the key for a title and author pair: 'normalized title|first surname'."""
    surnames = author_surnames(author)
    return f"{norm_title(title)}|{surnames[0] if surnames else ''}"


def book_id(work, key):
    """Return the site's book ID: the Open Library work ID, or 'x' plus a hash of the key."""
    if work:
        return work
    return "x" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]

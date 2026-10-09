"""Name fingerprints for matching products that have no barcode.

Weighed goods have no barcode by nature, and many listings simply omit it, so barcodes alone cannot
match a catalogue. The idea: reduce a name to the set of its significant words plus a size signature.
``Pomidor Çəhrayı 1kq 2512`` and ``POMİDOR ÇƏHRAYI KQ`` give the same fingerprint; ``Pomidor Salyan kq``
does not, because the variety differs.
"""

from __future__ import annotations

import re

from rpi.parsing import units

TRANS = str.maketrans(
    {
        "ə": "a",
        "ı": "i",
        "ö": "o",
        "ü": "u",
        "ç": "c",
        "ş": "s",
        "ğ": "g",
        "Ə": "a",
        "I": "i",
        "İ": "i",
        "Ö": "o",
        "Ü": "u",
        "Ç": "c",
        "Ş": "s",
        "Ğ": "g",
        # Cyrillic look-alikes that retailers mix with Latin letters inside one name
        "а": "a",
        "е": "e",
        "о": "o",
        "с": "c",
        "р": "p",
        "х": "x",
        "у": "y",
        "к": "k",
        "м": "m",
        "т": "t",
        "в": "v",
        "н": "n",
    }
)

# words that say nothing about the product
STOP = {
    "kq",
    "kg",
    "qr",
    "gr",
    "q",
    "g",
    "ml",
    "l",
    "lt",
    "eded",
    "ed",
    "adet",
    "st",
    "caki",
    "ceki",
    "cheki",
    "paket",
    "qutuda",
    "qutu",
    "setka",
    "tnk",
    "tk",
    "bag",
    "ile",
    "ve",
    "v",
    "i",
    "na",
    "dan",
    "den",
    "uchun",
    "ucun",
    "super",
    "lyuks",
    "luks",
    "yeni",
    "new",
    "aksiya",
    "endirim",
    "mehsul",
    "mehsullar",
    "no",
    "n",
    "x",
    "sm",
    "cm",
    "mm",
    "de",
    "da",
}

TOKEN = re.compile(r"[a-z0-9]+")


def tokens(name: str):
    s = (name or "").translate(TRANS).lower()
    s = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", s)  # fat percentage is handled separately
    # remove the whole size expression, otherwise "1kq" and "1l" would stay behind as words
    s = units.MULTI.sub(" ", s)
    s = units.AMOUNT.sub(" ", s)
    s = units.PACK.sub(" ", s)
    out = []
    for t in TOKEN.findall(s):
        if t in STOP:
            continue
        if t.isdigit():  # internal codes and size numbers
            continue
        if len(t) < 2:
            continue
        out.append(t)
    return out


def fat(name: str):
    """Fat percentage: 2,5% and 2.5% are the same, and it tells apart milks."""
    m = re.search(r"(\d{1,2}(?:[.,]\d)?)\s*%", name or "")
    return round(float(m.group(1).replace(",", ".")), 1) if m else None


def key(name: str):
    """The fingerprint, or None when there are too few words to claim anything."""
    t = tokens(name)
    if len(t) < 2:
        return None
    v, u, pack = units.parse(name)
    # "1 kq" on a price tag and a weighed product with no size are the same thing in practice
    if u == "g" and v == 1000:
        u, v = "kg_bulk", None
    return (tuple(sorted(set(t))), u, v, pack, fat(name))


def variant_digits(name: str):
    """Numbers left after removing sizes and percentages.

    These are product variants, not noise: a battery "2025" is not a "2032"; a hair colour number is a
    shade. ``tokens()`` drops pure numbers on purpose (that is where internal codes end up), but
    ``loose_key()`` must keep them, or it would merge products that must stay apart.
    """
    s = (name or "").translate(TRANS).lower()
    s = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", s)
    s = units.MULTI.sub(" ", s)
    s = units.AMOUNT.sub(" ", s)
    s = units.PACK.sub(" ", s)
    return tuple(sorted(t for t in TOKEN.findall(s) if t.isdigit()))


def loose_key(name: str):
    """A fingerprint tolerant of a space inside the brand name.

    Retailers write one brand differently: ``Azərsüd Süd`` and ``Azər Süd`` are the same milk, but the
    exact fingerprints differ (``azarsud``+``sud`` against ``azar``+``sud``) and trigram similarity
    (0.62) is below any safe threshold. Trick: drop any word that is fully contained in another word
    of the same name and glue the rest together:

        ('azarsud', 'sud') -> 'sud' is inside 'azarsud'  -> 'azarsud'
        ('azar', 'sud')    -> neither is inside the other -> 'azarsud'

    Size, pack, fat percentage and numeric variants stay in the key, otherwise it would merge 20% and
    25% sour cream, or battery 2025 with 2032.

    The key is deliberately coarser than ``key()``, so it may only be used *between different*
    retailers: identical names inside one chain almost always mean different articles.
    """
    k = key(name)
    if k is None:
        return None
    t, u, v, pack, f = k
    keep = [w for w in t if not any(w != o and w in o for o in t)]
    glued = "".join(sorted(keep or t))
    return (glued, u, v, pack, f, variant_digits(name))

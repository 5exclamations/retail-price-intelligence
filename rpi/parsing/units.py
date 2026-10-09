"""Quantity parsing from product names (Azerbaijani, Russian and English mixed).

``parse(name)`` returns ``(value, unit, pack)`` where unit is one of ``g``, ``ml``, ``pcs`` or
``kg_bulk``. Weights and volumes are normalised to grams and millilitres so that ``1 kq`` and ``1000 q``
are the same number. ``kg_bulk`` means a weighed product sold per kilogram: it has no fixed size.

Regional conventions handled here:

* ``qr`` / ``q`` / ``gr``: grams;  ``kq``: kilograms;  ``lt`` / ``l``: litres
* a bare trailing ``kq`` with no number: weighed product
* ``7 Lİ``, ``10 LU``, ``6-LI``: a pack of N pieces
* ``6X1.5 L``: multipack (6 bottles of 1.5 litres); dimensions such as ``120X200CM`` are not sizes
"""

from __future__ import annotations

import re

# Longer aliases first, otherwise 'q' would swallow 'qr'.
UNIT_ALIASES = [
    ("kq", "kg"),
    ("kg", "kg"),
    ("кг", "kg"),
    ("qram", "g"),
    ("qr", "g"),
    ("gr", "g"),
    ("gram", "g"),
    ("г", "g"),
    ("q", "g"),
    ("g", "g"),
    ("ml", "ml"),
    ("мл", "ml"),
    ("litr", "l"),
    ("lt", "l"),
    ("л", "l"),
    ("l", "l"),
    ("ədəd", "pcs"),
    ("eded", "pcs"),
    ("ədd", "pcs"),
    ("əd", "pcs"),
    ("adet", "pcs"),
    ("şt", "pcs"),
    ("st", "pcs"),
    ("pcs", "pcs"),
    ("pc", "pcs"),
]
_ALT = "|".join(a for a, _ in UNIT_ALIASES)
_TO = dict(UNIT_ALIASES)

# 500 QR / 1,5 L / 90ML / 300qr
AMOUNT = re.compile(rf"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*({_ALT})(?![a-zçəğıöşü])", re.IGNORECASE)
# 6X1.5 L, 2x200 ml
MULTI = re.compile(rf"(\d+)\s*[xх*]\s*(\d+(?:[.,]\d+)?)\s*({_ALT})(?![a-zçəğıöşü])", re.IGNORECASE)
# 7 Lİ / 10 LU / 6-LI / 12-li  (pack of N pieces)
PACK = re.compile(r"(?<![\d.,])(\d{1,3})\s*[-\s]?(l[ıiuü])(?![a-zçəğıöşü])", re.IGNORECASE)
# trailing "kq" / "kg" without a number = weighed product
BULK = re.compile(r"(?:^|\s)(kq|kg|кг)(?:\s|$)", re.IGNORECASE)
# Dimensions are not pack sizes: three numbers joined by x, or two numbers followed by sm/cm.
# A plain "number x number" must not match, or the multipack 6X1.5 L would be eaten.
DIMS = re.compile(
    r"\d+(?:[.,]\d+)?\s*[xх]\s*\d+(?:[.,]\d+)?\s*[xх]\s*\d+(?:[.,]\d+)?"
    r"|\d+(?:[.,]\d+)?\s*[xх]\s*\d+(?:[.,]\d+)?\s*(?:sm|cm|см)\b",
    re.IGNORECASE,
)


def parse(name: str):
    if not name:
        return None, None, None
    s = DIMS.sub(" ", name)  # drop dimensions before anything else
    pack = None

    m = MULTI.search(s)
    if m:
        pack = int(m.group(1))
        val = float(m.group(2).replace(",", "."))
        unit = _TO[m.group(3).lower()]
        return _base(val, unit) + (pack,)

    p = PACK.search(s)
    if p:
        pack = int(p.group(1))
        s = s[: p.start()] + " " + s[p.end() :]

    m = AMOUNT.search(s)
    if m:
        val = float(m.group(1).replace(",", "."))
        unit = _TO[m.group(2).lower()]
        v, u = _base(val, unit)
        return v, u, pack

    if BULK.search(s):
        return None, "kg_bulk", pack
    if pack:
        return float(pack), "pcs", pack
    return None, None, None


def _base(val, unit):
    """Convert to grams and millilitres so that 1 kq and 1000 q are the same number."""
    if unit == "kg":
        return val * 1000, "g"
    if unit == "l":
        return val * 1000, "ml"
    return val, unit

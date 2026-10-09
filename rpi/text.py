"""Text normalisation for Azerbaijani / Turkish product names.

The trap this module exists for: ``'İ'.lower()`` is ``'i' + U+0307`` (a combining dot), so
``'milla' in 'MİLLA'.lower()`` is False. Turkic letters are therefore mapped to ASCII *before*
lower-casing, and combining marks are dropped afterwards (NFD).
"""

from __future__ import annotations

import re
import unicodedata

_TURKIC = str.maketrans(
    {
        "İ": "I",
        "ı": "i",
        "Ə": "A",
        "ə": "a",
        "Ö": "O",
        "ö": "o",
        "Ü": "U",
        "ü": "u",
        "Ç": "C",
        "ç": "c",
        "Ş": "S",
        "ş": "s",
        "Ğ": "G",
        "ğ": "g",
    }
)
_SPACES = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    s = value.translate(_TURKIC).lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return _SPACES.sub(" ", s).strip()


def to_qepik(value: object) -> int | None:
    """Convert a decimal AZN amount (str / float / int) to integer qepik without float arithmetic.

    Decimal(str(x)) keeps ``3.49`` exact; ROUND_HALF_UP avoids banker's rounding surprises.
    Returns None for anything that is not a finite number; negatives are returned so that the caller
    can reject them with a precise reason.
    """
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def valid_gtin(code: str | None) -> bool:
    """GTIN-8/12/13/14 check digit validation."""
    if not code or not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check

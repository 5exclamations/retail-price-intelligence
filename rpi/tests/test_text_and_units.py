"""Text handling: Turkic capitals, exact money, regional unit abbreviations, fingerprints."""

from __future__ import annotations

import pytest

from rpi.parsing import fingerprint, units
from rpi.text import normalize_text, to_qepik, valid_gtin


def test_turkic_dotted_capital_i_does_not_break_matching():
    # 'İ'.lower() is 'i' + U+0307; naive normalisation made this False.
    assert "milla" in normalize_text("MİLLA ŞOKOLAD")
    assert normalize_text("Şəfəq Süd") == normalize_text("ŞƏFƏQ SÜD") == "safaq sud"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3.49", 349),
        (3.49, 349),
        ("3,49", 349),
        (0.1 + 0.2, 30),
        ("12", 1200),
        ("0.005", 1),
        (-2.5, -250),
        ("n/a", None),
        ("", None),
        (None, None),
        (True, None),
        ("nan", None),
        ("inf", None),
    ],
)
def test_money_is_converted_to_integer_qepik_without_float_drift(raw, expected):
    assert to_qepik(raw) == expected


def test_gtin_check_digit():
    assert valid_gtin("4006381333931")
    assert not valid_gtin("4006381333932")
    assert not valid_gtin("123")
    assert not valid_gtin(None)


def test_regional_unit_abbreviations():
    assert units.parse("Tufan Qara Çay 200qr")[:2] == (200, "g")  # qr = grams
    assert units.parse("Kələm Qırmızı kq")[:2] == (None, "kg_bulk")  # bare kq = weighed
    assert units.parse("MOLPED QADIN BEZİ 7 Lİ")[0] == 7  # pack of 7
    assert units.parse("Su 1,5 L")[:2] == (1500, "ml")


def test_fingerprint_separates_what_must_stay_apart():
    key = fingerprint.key
    assert key("Milla Süd 2,5% 1 l") == key("MİLLA SÜD 2.5% 1L")
    assert key("Milla Süd 3,2% 1 l") != key("MİLLA SÜD 2.5% 1L")  # fat percentage differs
    assert key("OMAN UN 1 KQ") != key("OMAN UN 4 KQ")  # pack size differs

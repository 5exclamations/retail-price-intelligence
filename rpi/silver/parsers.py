"""Per-source parsers: raw bronze payload -> one canonical dict.

Each retailer names fields differently and encodes money differently (decimal strings, float AZN,
integer qepik). All of them are converted to integer qepik here, once, without float arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from rpi.text import to_qepik

BAKU = ZoneInfo("Asia/Baku")


@dataclass
class Parsed:
    sku: str | None = None
    name: str | None = None
    brand: str | None = None
    ean: str | None = None
    category_raw: str | None = None
    price: int | None = None
    old_price: int | None = None
    available: bool = True
    store_code: str | None = None
    observed_at: datetime | None = None
    promo_until: date | None = None
    error: str | None = None


def _s(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ts(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), BAKU)
        return datetime.fromisoformat(str(value).strip()).replace(tzinfo=BAKU)
    except (ValueError, OSError, OverflowError):
        return None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _int_qepik(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return to_qepik(value) if isinstance(value, str) and "." in value else None


def parse_baku_fresh(p: dict) -> Parsed:
    return Parsed(
        sku=_s(p.get("sku")),
        name=_s(p.get("title")),
        brand=_s(p.get("brand")),
        ean=_s(p.get("barcode")),
        category_raw=_s(p.get("cat")),
        price=to_qepik(p.get("price")),
        old_price=to_qepik(p.get("was")),
        available=bool(p.get("in_stock", True)),
        observed_at=_ts(p.get("scraped_at")),
    )


def parse_caspianmart(p: dict) -> Parsed:
    return Parsed(
        sku=_s(p.get("id")),
        name=_s(p.get("name")),
        ean=_s(p.get("ean")),
        category_raw=_s(p.get("category_path")),
        price=_int_qepik(p.get("current_price_qepik")),
        old_price=_int_qepik(p.get("regular_price_qepik")),
        available=(p.get("stock") or 0) > 0,
        observed_at=_ts(p.get("ts")),
    )


def parse_absheron(p: dict) -> Parsed:
    return Parsed(
        sku=_s(p.get("item_code")),
        name=_s(p.get("description")),
        brand=_s(p.get("brand")),
        ean=_s(p.get("gtin")),
        category_raw=_s(p.get("cat_id")),
        price=to_qepik(p.get("price_azn")),
        old_price=to_qepik(p.get("old_price_azn")),
        available=bool(p.get("available", True)),
        store_code=_s(p.get("store_code")),
        observed_at=_ts(p.get("updated")),
        promo_until=_date(p.get("promo_end")),
    )


def parse_shirvan(p: dict) -> Parsed:
    return Parsed(
        sku=_s(p.get("code")),
        name=_s(p.get("product_name")),
        brand=_s(p.get("brand")),
        ean=_s(p.get("barcode")),
        category_raw=_s(p.get("department")),
        price=to_qepik(p.get("price")),
        old_price=to_qepik(p.get("list_price")),
        available=str(p.get("available", "Y")).upper() == "Y",
        observed_at=_ts(p.get("timestamp")),
    )


def parse_sumqayit(p: dict) -> Parsed:
    prod, offer, meta = p.get("product") or {}, p.get("offer") or {}, p.get("meta") or {}
    return Parsed(
        sku=_s(prod.get("code")),
        name=_s(prod.get("name")),
        brand=_s(prod.get("brand")),
        ean=_s(prod.get("barcode")),
        category_raw=_s(prod.get("group")),
        price=to_qepik(offer.get("price")),
        old_price=to_qepik(offer.get("strike_price")),
        available=bool(offer.get("in_stock", True)),
        observed_at=_ts(meta.get("fetched")),
    )


PARSERS = {
    "baku_fresh": parse_baku_fresh,
    "caspianmart": parse_caspianmart,
    "absheron": parse_absheron,
    "shirvan": parse_shirvan,
    "sumqayit": parse_sumqayit,
}


def parse_record(source: str, payload: dict) -> Parsed:
    if "_unparseable" in payload:
        return Parsed(error="unparseable_line")
    try:
        return PARSERS[source](payload)
    except (TypeError, ValueError, AttributeError) as exc:  # a shape we have never seen
        return Parsed(error=f"unexpected_shape:{type(exc).__name__}")

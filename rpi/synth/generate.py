"""Deterministic synthetic retailer feeds.

``generate`` simulates a full price history for every (retailer, product) pair, then writes one landing
file per retailer and day in that retailer's own format (JSONL, JSON array, CSV; different field names,
price encodings and category labels). The same seed always produces byte-identical files.

Alongside the feeds it writes a *ground truth* (which SKU is which canonical product) so the matcher
can be scored, and a manifest of every defect that was injected so data-quality checks can be verified
against known counts. Ground truth never enters the pipeline.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from rpi.synth.catalog import CATEGORY_MONTHLY_DRIFT, Product, build_catalog

REFERENCE = Path(__file__).resolve().parent.parent / "reference"

# retailer: assortment coverage, price bias vs the market, share of rows with a barcode, promo start
# probability per day, typo rate in names, price ending convention, file format, fetch time.
RETAILERS: dict[str, dict] = {
    "baku_fresh": dict(
        coverage=0.75,
        bias=0.000,
        ean_rate=0.60,
        promo=0.008,
        typo=0.010,
        ending="nines",
        ext="jsonl",
        at=(6, 10),
        brand_rate=1.0,
        lag=0,
    ),
    "caspianmart": dict(
        coverage=0.80,
        bias=-0.015,
        ean_rate=0.92,
        promo=0.010,
        typo=0.004,
        ending="nines",
        ext="jsonl",
        at=(5, 40),
        brand_rate=0.0,
        lag=2,
    ),
    "absheron": dict(
        coverage=0.70,
        bias=0.010,
        ean_rate=0.70,
        promo=0.007,
        typo=0.008,
        ending="fives",
        ext="json",
        at=(6, 14),
        brand_rate=0.5,
        lag=1,
    ),
    "shirvan": dict(
        coverage=0.50,
        bias=0.060,
        ean_rate=0.45,
        promo=0.014,
        typo=0.006,
        ending="fives",
        ext="csv",
        at=(7, 5),
        brand_rate=0.9,
        lag=3,
    ),
    "sumqayit": dict(
        coverage=0.60,
        bias=-0.040,
        ean_rate=0.05,
        promo=0.009,
        typo=0.030,
        ending="nines",
        ext="jsonl",
        at=(4, 55),
        brand_rate=0.7,
        lag=0,
    ),
}

# Prices are simulated on a fixed horizon, independent of the requested window, so extending the window
# later ("tomorrow's files") continues exactly the same price histories.
HORIZON_START = date(2026, 1, 1)
HORIZON_DAYS = 500

ZONE_FACTOR = {"A": 1.00, "B": 1.04, "C": 1.07, "D": 0.98}
DISTRICTS = [
    "Nəsimi",
    "Yasamal",
    "Nizami",
    "Xətai",
    "Binəqədi",
    "Sabunçu",
    "Suraxanı",
    "Səbail",
    "Nərimanov",
    "Qaradağ",
    "Pirallahı",
    "Xəzər",
]
STORE_FORMATS = ["Hypermarket", "Supermarket", "Express"]

_AZ_UPPER = str.maketrans({"i": "İ", "ı": "I"})


@dataclass
class GenConfig:
    seed: int = 42
    n_products: int = 320
    days: int = 90
    end_date: date = date(2026, 9, 30)
    landing_dir: Path = Path("data/landing")
    truth_dir: Path = Path("data/truth")
    from_date: date | None = (
        None  # write only files in [from_date, to_date]; simulation is unaffected
    )
    to_date: date | None = None
    outage: dict[str, tuple[int, int]] = field(default_factory=lambda: {"sumqayit": (40, 41)})
    incidents: list[str] = field(
        default_factory=list
    )  # e.g. "unit_mismatch:caspianmart:2026-09-30"
    clean: bool = False  # disable defect injection (useful for exact-count tests)

    @property
    def start_date(self) -> date:
        return self.end_date - timedelta(days=self.days - 1)


@dataclass
class Row:
    sku: str
    product: Product
    name: str
    brand: str | None
    ean: str | None
    category_raw: str
    price: int
    old_price: int | None
    available: bool
    promo_until: date | None
    store_code: str | None
    fetched_at: datetime


# --------------------------------------------------------------------------------------- names


def _make_sku(retailer: str, rng: random.Random, pid: int) -> str:
    if retailer == "baku_fresh":
        return f"BF-{rng.randrange(10**6):06d}"
    if retailer == "caspianmart":
        return str(rng.randrange(10**7, 10**8))
    if retailer == "absheron":
        return f"A{rng.randrange(10**5):05d}"
    if retailer == "shirvan":
        return f"SH{rng.randrange(10**4):04d}-{pid % 97}"
    return f"{rng.randrange(16**8):08x}"


def _typo(rng: random.Random, text: str) -> str:
    letters = [i for i, c in enumerate(text) if c.isalpha()]
    if len(letters) < 5:
        return text
    i = rng.choice(letters[1:-1])
    if rng.random() < 0.5:
        return text[:i] + text[i + 1 :]  # dropped letter
    return text[:i] + text[i + 1] + text[i] + text[i + 2 :] if i + 2 < len(text) else text


def _size(p: Product, style: str) -> str:
    v, u = p.unit_value, p.unit_type
    if p.weighed:
        return {"caspianmart": "KQ", "shirvan": "kg"}.get(style, "kq")
    if u == "pcs":
        if p.pack:  # rolls / bags: sold as "8 Lİ"
            return {"caspianmart": f"{v} LI", "absheron": f"{v}-li", "shirvan": f"{v} lı"}.get(
                style, f"{v} li"
            )
        return {"caspianmart": f"{v} ƏDƏD", "absheron": f"{v} ədəd"}.get(style, f"{v} ədəd")
    big = v >= 1000
    if style == "caspianmart":
        return (
            f"{v / 1000:g}L"
            if u == "ml" and big
            else f"{v / 1000:g}KQ"
            if big
            else f"{v}{'ML' if u == 'ml' else 'Q'}"
        )
    if style == "absheron":
        return (
            f"{v / 1000:g} lt"
            if u == "ml" and big
            else f"{v / 1000:g} kq"
            if big
            else f"{v} {'ml' if u == 'ml' else 'qr'}"
        )
    if style == "shirvan":
        return f"{v} {'ml' if u == 'ml' else 'gr'}"
    if style == "sumqayit":
        return (
            f"{v / 1000:g}l"
            if u == "ml" and big
            else f"{v / 1000:g}kq"
            if big
            else f"{v}{'ml' if u == 'ml' else 'q'}"
        )
    return (
        f"{v / 1000:g} {'l' if u == 'ml' else 'kq'}" if big else f"{v} {'ml' if u == 'ml' else 'q'}"
    )


def render_name(
    p: Product, style: str, rng: random.Random, typo_rate: float
) -> tuple[str, str | None]:
    """Name as printed by a retailer, and the brand field it would send (or None)."""
    variant = p.variant
    if variant.endswith("%"):
        num = variant[:-1]
        variant = {"absheron": f"{num.replace('.', ',')}%", "shirvan": f"{num} %"}.get(
            style, variant
        )
    brand = p.brand
    size = _size(p, style)
    if style == "sumqayit":
        parts = [p.type_name, variant, brand, size]  # type first, brand after
        if brand and rng.random() < 0.06:
            parts[2] = None  # brand left out: genuinely ambiguous
    elif style == "absheron":
        parts = [
            brand,
            p.type_name.lower() if brand else p.type_name,
            variant.lower() if variant and not variant[0].isdigit() else variant,
            size,
        ]
    else:
        parts = [brand, p.type_name, variant, size]
    name = " ".join(x for x in parts if x)
    if style == "caspianmart":
        name = name.translate(_AZ_UPPER).upper()
    if rng.random() < typo_rate:
        name = _typo(rng, name)
    return name, brand


# --------------------------------------------------------------------------------------- prices


def _ending(price: float, style: str) -> int:
    p = max(int(round(price)), 20)
    if style == "nines" and p >= 100:
        return p // 10 * 10 + 9 if p % 10 >= 5 else max(p // 10 * 10 - 1, 9)
    return max(int(round(p / 5)) * 5, 5)


def _regular_series(
    p: Product,
    retailer: str,
    cfg: dict,
    shared: bool,
    rng: np.random.Generator,
    days: int,
    start: date,
) -> np.ndarray:
    drift = CATEGORY_MONTHLY_DRIFT[p.category]
    t = np.arange(days) - (0 if shared else cfg["lag"])
    infl = (1 + drift) ** (np.maximum(t, 0) / 30.0)
    if p.category == "Fruit & Vegetables":  # seasonal swing
        doy = np.array([(start + timedelta(days=int(i))).timetuple().tm_yday for i in range(days)])
        infl = infl * (1 + 0.10 * np.sin(2 * math.pi * doy / 365.0 + 1.1))
    factor = 1.0 if shared else 1.0 + cfg["bias"] + rng.normal(0, 0.02)
    # Retailer-specific repricing events: a few small steps per year, kept within +-15% of the list
    # price so that retailers stay comparable (real shelf prices are sticky, not a random walk).
    steps = np.ones(days)
    for d in np.nonzero(rng.random(days) < 0.004)[0]:
        steps[d:] *= 1 + rng.choice([-1, 1]) * rng.uniform(0.02, 0.06)
    steps = np.clip(steps, 0.85, 1.15)
    return p.base_price * factor * infl * steps


def _simulate_item(
    p: Product,
    retailer: str,
    cfg: dict,
    shared: bool,
    seed: int,
    shared_seed: int,
    days: int,
    start: date,
    clean: bool,
):
    # Products on a "common list price" (about 60% of them)
    # get the same regular-price path at every retailer: same stream, same ending convention, no lag.
    # Promotions always come from the retailer's own stream.
    rng = np.random.default_rng(seed)
    reg_rng = np.random.default_rng(shared_seed) if shared else rng
    reg = _regular_series(p, retailer, cfg, shared, reg_rng, days, start)
    ending = "nines" if shared else cfg["ending"]
    price = np.zeros(days, dtype=np.int64)
    old = np.zeros(days, dtype=np.int64)
    promo_end = [None] * days
    remaining, depth, inflated = 0, 0.0, False
    for d in range(days):
        regular = _ending(reg[d], ending)
        if remaining == 0 and rng.random() < cfg["promo"]:
            remaining = int(rng.integers(3, 15))
            depth = float(np.clip(rng.gamma(2.2, 0.11), 0.08, 0.55))
            inflated = rng.random() < 0.05
        if remaining > 0:
            if (
                inflated
            ):  # fake promotion: a high "old" price, sale price roughly at the market level
                old[d] = _ending(regular * rng.uniform(1.35, 1.8), ending)
                price[d] = _ending(regular * rng.uniform(0.95, 1.0), ending)
            else:
                old[d] = regular
                price[d] = _ending(regular * (1 - depth), ending)
            promo_end[d] = d + remaining - 1
            remaining -= 1
        else:
            price[d] = regular
    return price, old, promo_end, rng


# --------------------------------------------------------------------------------------- renderers


def _render(retailer: str, rows: list[Row]) -> tuple[str, bytes]:
    """Return (extension, bytes) for a day's rows in the retailer's native format."""
    if retailer == "baku_fresh":
        lines = [
            json.dumps(
                {
                    "sku": r.sku,
                    "title": r.name,
                    "brand": r.brand,
                    "barcode": r.ean,
                    "cat": r.category_raw,
                    "price": f"{r.price // 100}.{r.price % 100:02d}",
                    "was": None
                    if r.old_price is None
                    else f"{r.old_price // 100}.{r.old_price % 100:02d}",
                    "in_stock": r.available,
                    "scraped_at": r.fetched_at.isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            for r in rows
        ]
        return "jsonl", ("\n".join(lines) + "\n").encode()
    if retailer == "caspianmart":
        lines = [
            json.dumps(
                {
                    "id": int(r.sku),
                    "name": r.name,
                    "ean": r.ean,
                    "category_path": r.category_raw,
                    "current_price_qepik": r.price,
                    "regular_price_qepik": r.old_price,
                    "stock": 12 if r.available else 0,
                    "ts": int(r.fetched_at.timestamp()),
                },
                ensure_ascii=False,
            )
            for r in rows
        ]
        return "jsonl", ("\n".join(lines) + "\n").encode()
    if retailer == "absheron":
        data = [
            {
                "item_code": r.sku,
                "store_code": r.store_code,
                "description": r.name,
                "brand": r.brand,
                "gtin": r.ean,
                "cat_id": r.category_raw,
                "price_azn": r.price
                / 100,  # float on the wire: the silver layer must convert exactly
                "old_price_azn": None if r.old_price is None else r.old_price / 100,
                "promo_end": r.promo_until.isoformat() if r.promo_until else None,
                "available": r.available,
                "updated": r.fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for r in rows
        ]
        return "json", json.dumps(data, ensure_ascii=False).encode()
    if retailer == "shirvan":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(
            [
                "code",
                "product_name",
                "brand",
                "barcode",
                "department",
                "price",
                "list_price",
                "available",
                "timestamp",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.sku,
                    r.name,
                    r.brand or "",
                    r.ean or "",
                    r.category_raw,
                    f"{r.price // 100}.{r.price % 100:02d}",
                    "" if r.old_price is None else f"{r.old_price // 100}.{r.old_price % 100:02d}",
                    "Y" if r.available else "N",
                    r.fetched_at.isoformat(timespec="seconds"),
                ]
            )
        return "csv", buf.getvalue().encode()
    lines = [
        json.dumps(
            {
                "product": {
                    "code": r.sku,
                    "name": r.name,
                    "brand": r.brand,
                    "barcode": r.ean,
                    "group": r.category_raw,
                },
                "offer": {
                    "price": r.price / 100,
                    "strike_price": None if r.old_price is None else r.old_price / 100,
                    "in_stock": r.available,
                },
                "meta": {"fetched": r.fetched_at.isoformat(timespec="seconds")},
            },
            ensure_ascii=False,
        )
        for r in rows
    ]
    return "jsonl", ("\n".join(lines) + "\n").encode()


def _category_labels() -> dict[tuple[str, str], str]:
    out = {}
    with open(REFERENCE / "category_map.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[(r["retailer_code"], r["category"])] = r["raw_category"]
    return out


# --------------------------------------------------------------------------------------- main


def generate(cfg: GenConfig) -> dict:
    rng = random.Random(cfg.seed)
    catalog = build_catalog(cfg.n_products, cfg.seed)
    labels = _category_labels()
    days = cfg.days
    start = cfg.start_date
    if start < HORIZON_START or cfg.end_date > HORIZON_START + timedelta(days=HORIZON_DAYS - 1):
        raise ValueError(f"window {start}..{cfg.end_date} lies outside the simulation horizon")
    dates = [start + timedelta(days=i) for i in range(days)]

    # Stores of the zoned retailer. Zones are independent of store format on purpose.
    zones = ["A", "B", "C", "D"]
    stores = []
    for i in range(8):
        zone = zones[i % 4]
        stores.append(
            {
                "store_code": f"S{i + 1:02d}",
                "name": f"Absheron Market {DISTRICTS[i]}",
                "format": rng.choice(STORE_FORMATS),
                "price_zone": zone,
            }
        )

    # Assortment per retailer.
    assort: dict[str, list[Product]] = {r: [] for r in RETAILERS}
    for p in catalog:
        chosen = [r for r, c in RETAILERS.items() if rng.random() < c["coverage"]]
        if not chosen:
            chosen = [rng.choice(list(RETAILERS))]
        for r in chosen:
            assort[r].append(p)

    # SKUs, names, barcodes: fixed per (retailer, product) for the whole window.
    items: dict[tuple[str, int], dict] = {}
    counters = Counter()
    used_skus: set[tuple[str, str]] = set()
    for r, plist in assort.items():
        cfg_r = RETAILERS[r]
        for p in plist:
            irng = random.Random(f"{cfg.seed}-{r}-{p.pid}")
            while True:  # SKUs are unique per retailer, as real catalogue ids are
                sku = _make_sku(r, irng, p.pid)
                if (r, sku) not in used_skus:
                    used_skus.add((r, sku))
                    break
            name, brand = render_name(p, r, irng, 0.0 if cfg.clean else cfg_r["typo"])
            if p.weighed:
                # weighed goods carry an in-store code at best, never a global barcode
                ean = f"2{irng.randrange(10**11):011d}0" if irng.random() < 0.3 else None
            else:
                ean = p.ean if irng.random() < cfg_r["ean_rate"] else None
            category_raw = labels[(r, p.category)]
            if r == "caspianmart":
                category_raw = f"{category_raw} > {p.type_name}"
            items[(r, p.pid)] = dict(
                sku=sku,
                name=name,
                ean=ean,
                category_raw=category_raw,
                wrong_ean=False,
                brand=brand if irng.random() < cfg_r["brand_rate"] else None,
            )
    # Seed a few barcode collisions (same barcode on two different products): data-entry errors.
    if not cfg.clean:
        eligible = [
            (r, p)
            for (r, pid), it in items.items()
            for p in [catalog[pid - 1]]
            if it["ean"] and not p.weighed
        ]
        for r, p in rng.sample(eligible, k=min(3, len(eligible))):
            twin = next(
                (
                    q
                    for q in catalog
                    if q.category == p.category
                    and q.pid != p.pid
                    and q.ean
                    and q.unit_type == p.unit_type
                    and q.unit_value != p.unit_value
                ),
                None,
            )
            if twin:
                items[(r, p.pid)]["ean"] = twin.ean
                items[(r, p.pid)]["wrong_ean"] = True
                counters["ean_collision"] += 1

    # Price simulation (full window, regardless of which days get written).
    series: dict[tuple[str, int], tuple] = {}
    shared = {p.pid: rng.random() < 0.60 for p in catalog}
    for (r, pid), _ in items.items():
        p = catalog[pid - 1]
        series[(r, pid)] = _simulate_item(
            p,
            r,
            RETAILERS[r],
            shared[pid],
            zlib.crc32(f"{cfg.seed}-{r}-{pid}".encode()),
            zlib.crc32(f"{cfg.seed}-shared-{pid}".encode()),
            HORIZON_DAYS,
            HORIZON_START,
            cfg.clean,
        )

    # Ground truth.
    cfg.truth_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.truth_dir / "products.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pid", "canonical_name", "brand", "category", "unit_value", "unit_type", "ean"])
        for p in catalog:
            w.writerow(
                [
                    p.pid,
                    p.canonical_name,
                    p.brand or "",
                    p.category,
                    p.unit_value or "",
                    p.unit_type,
                    p.ean or "",
                ]
            )
    with open(cfg.truth_dir / "items.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["retailer_code", "sku", "pid", "wrong_ean"])
        for (r, pid), it in sorted(items.items()):
            w.writerow([r, it["sku"], pid, int(it["wrong_ean"])])
    with open(cfg.truth_dir / "stores.json", "w", encoding="utf-8") as f:
        json.dump(stores, f, ensure_ascii=False, indent=1)

    incidents: dict[tuple[str, date], str] = {}
    for spec in cfg.incidents:
        kind, src, day = spec.split(":")
        incidents[(src, date.fromisoformat(day))] = kind

    lo = cfg.from_date or start
    hi = cfg.to_date or cfg.end_date
    written: list[str] = []
    for di, day in enumerate(dates):
        if not lo <= day <= hi:
            continue
        for r, cfg_r in RETAILERS.items():
            if r in cfg.outage and cfg.outage[r][0] <= di <= cfg.outage[r][1] and not cfg.clean:
                counters["outage_files"] += 1
                continue
            fetched = datetime(day.year, day.month, day.day, cfg_r["at"][0], cfg_r["at"][1], 0)
            drng = random.Random(f"{cfg.seed}-{r}-{day}")
            rows: list[Row] = []
            for p in assort[r]:
                it = items[(r, p.pid)]
                price, old, promo_end, _ = series[(r, p.pid)]
                hd = (day - HORIZON_START).days
                price_d, old_d = int(price[hd]), int(old[hd]) or None
                pe = promo_end[hd]
                promo_until = (HORIZON_START + timedelta(days=int(pe))) if pe is not None else None
                avail = drng.random() > 0.03
                if r == "absheron":
                    for s in stores:
                        f = ZONE_FACTOR[s["price_zone"]]
                        sp = price_d if f == 1.0 else _ending(price_d * f, "fives")
                        so = (old_d if f == 1.0 else _ending(old_d * f, "fives")) if old_d else None
                        if not cfg.clean and drng.random() < 0.002:
                            sp += 10  # one store lags behind its zone
                            counters["zone_price_lag"] += 1
                        rows.append(
                            Row(
                                it["sku"],
                                p,
                                it["name"],
                                it["brand"],
                                it["ean"],
                                it["category_raw"],
                                sp,
                                so,
                                avail,
                                promo_until,
                                s["store_code"],
                                fetched,
                            )
                        )
                else:
                    rows.append(
                        Row(
                            it["sku"],
                            p,
                            it["name"],
                            it["brand"],
                            it["ean"],
                            it["category_raw"],
                            price_d,
                            old_d,
                            avail,
                            promo_until,
                            None,
                            fetched,
                        )
                    )
            kind = incidents.get((r, day))
            if kind == "unit_mismatch":  # feed reports qepik where AZN is expected (x100)
                for row in rows:
                    row.price *= 100
                    row.old_price = None if row.old_price is None else row.old_price * 100
            if kind == "truncated":
                rows = rows[: max(1, len(rows) // 5)]
            if not cfg.clean and not kind:
                rows = _inject_defects(rows, drng, counters, r)
            ext, payload = _render(r, rows)
            if ext == "jsonl" and not cfg.clean and not kind:
                payload = _inject_broken_line(payload, drng, counters)
            out_dir = cfg.landing_dir / r
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{r}_{day.isoformat()}.{ext}"
            path.write_bytes(payload)
            written.append(str(path))
        if "absheron" in RETAILERS and di == 0 and lo <= day <= hi:
            (cfg.landing_dir / "absheron").mkdir(parents=True, exist_ok=True)
            (cfg.landing_dir / "absheron" / f"absheron_stores_{day.isoformat()}.json").write_text(
                json.dumps(stores, ensure_ascii=False), encoding="utf-8"
            )
    manifest = {
        "seed": cfg.seed,
        "products": len(catalog),
        "items": len(items),
        "files": len(written),
        "injected": dict(counters),
        "start": start.isoformat(),
        "end": cfg.end_date.isoformat(),
    }
    (cfg.truth_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


# Defect injection --------------------------------------------------------------------------------


def _inject_defects(
    rows: list[Row], rng: random.Random, counters: Counter, retailer: str
) -> list[Row]:
    """At most one defect per row, and only clean rows are duplicated, so every injected defect maps
    to exactly one rejected row in silver (the tests rely on that)."""
    out: list[Row] = []
    for r in rows:
        x = rng.random()
        if x < 0.0015:
            r.price = 0
            counters["zero_price"] += 1
        elif x < 0.0025:
            r.price = -abs(r.price)
            counters["negative_price"] += 1
        elif x < 0.0040:
            r.name = ""
            counters["empty_name"] += 1
        out.append(r)
        if x >= 0.0040 and rng.random() < 0.01:
            out.append(r)  # exact duplicate line
            counters["duplicate_row"] += 1
    return out


def _inject_broken_line(payload: bytes, rng: random.Random, counters: Counter) -> bytes:
    lines = payload.decode().splitlines()
    if len(lines) > 20 and rng.random() < 0.15:
        i = rng.randrange(len(lines))
        if lines.count(lines[i]) == 1:  # never break a line that has a duplicate twin
            lines[i] = lines[i][: len(lines[i]) // 2]  # truncated JSON
            counters["unparseable_line"] += 1
    return ("\n".join(lines) + "\n").encode()

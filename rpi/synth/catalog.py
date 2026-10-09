"""Synthetic product catalogue.

Everything here is invented: retailer names, brand names and prices are fictional, so nothing in this
repository describes a real company's pricing. The *structure* mirrors what real Azerbaijani retail
feeds look like (mixed Azerbaijani/Russian/English labels, Turkic capitals, packs and weighed goods).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

CATEGORIES = [
    "Dairy & Eggs",
    "Bakery",
    "Beverages",
    "Snacks & Sweets",
    "Pantry Staples",
    "Meat & Poultry",
    "Fruit & Vegetables",
    "Household",
    "Personal Care",
    "Frozen",
]

BRANDS = [
    "Sutlu",
    "Qafqaz",
    "Xəzər",
    "Nərgiz",
    "Gilan",
    "Altun",
    "Bahar",
    "Dəniz",
    "Zəfər",
    "Şəfəq",
    "Mavi",
    "Aypara",
    "Ceyran",
    "Tufan",
    "Ulduz",
    "Yaşıl",
    "Çinar",
    "Ərzaq Plus",
    "Sahil",
    "Günəş",
]

# category -> list of (product type, variant tokens, sizes, price range qepik, weighed)
# sizes are (value, unit) in base units: g / ml / pcs. Weighed goods have no size and a per-kg price.
_T = [
    ("Dairy & Eggs", "Süd", ["1.5%", "3.2%"], [(500, "ml"), (1000, "ml")], (170, 420), False),
    ("Dairy & Eggs", "Qatıq", [""], [(500, "g"), (900, "g")], (150, 380), False),
    ("Dairy & Eggs", "Kefir", [""], [(500, "ml"), (1000, "ml")], (190, 360), False),
    ("Dairy & Eggs", "Xama", ["15%", "20%"], [(200, "g"), (400, "g")], (190, 480), False),
    ("Dairy & Eggs", "Kərə yağı", ["72.5%", "82.5%"], [(180, "g"), (200, "g")], (390, 850), False),
    ("Dairy & Eggs", "Pendir", [""], [(300, "g"), (500, "g")], (480, 1450), False),
    ("Dairy & Eggs", "Yumurta", [""], [(10, "pcs"), (20, "pcs")], (320, 900), False),
    ("Bakery", "Lavaş", [""], [(300, "g"), (500, "g")], (60, 160), False),
    ("Bakery", "Kruasan", ["Şokoladlı", "Vanilli"], [(60, "g"), (80, "g")], (70, 190), False),
    ("Bakery", "Peçenye", ["Kakaolu", "Süd"], [(150, "g"), (300, "g")], (130, 420), False),
    (
        "Beverages",
        "Su",
        ["Qazsız", "Qazlı"],
        [(500, "ml"), (1500, "ml"), (5000, "ml")],
        (35, 260),
        False,
    ),
    (
        "Beverages",
        "Limonad",
        ["Limon", "Albalı"],
        [(500, "ml"), (1000, "ml"), (1500, "ml")],
        (80, 290),
        False,
    ),
    (
        "Beverages",
        "Şirə",
        ["Alma", "Nar", "Portağal"],
        [(200, "ml"), (1000, "ml")],
        (95, 480),
        False,
    ),
    (
        "Beverages",
        "Çay",
        ["Qara", "Yaşıl"],
        [(100, "g"), (200, "g"), (400, "g")],
        (260, 1250),
        False,
    ),
    ("Beverages", "Qəhvə", ["Əsl", "3 in 1"], [(100, "g"), (200, "g")], (520, 1900), False),
    (
        "Snacks & Sweets",
        "Çips",
        ["Pendirli", "Qaymaqlı"],
        [(60, "g"), (150, "g")],
        (130, 460),
        False,
    ),
    ("Snacks & Sweets", "Şokolad", ["Südlü", "Bitter"], [(90, "g"), (200, "g")], (170, 720), False),
    ("Snacks & Sweets", "Biskvit", [""], [(200, "g"), (400, "g")], (140, 520), False),
    ("Snacks & Sweets", "Fındıq", [""], [(100, "g"), (250, "g")], (350, 1100), False),
    ("Pantry Staples", "Düyü", [""], [(900, "g"), (2000, "g"), (5000, "g")], (190, 1650), False),
    (
        "Pantry Staples",
        "Günəbaxan yağı",
        [""],
        [(1000, "ml"), (2000, "ml"), (5000, "ml")],
        (320, 1950),
        False,
    ),
    (
        "Pantry Staples",
        "Un",
        ["Ali növ"],
        [(1000, "g"), (2000, "g"), (5000, "g")],
        (110, 750),
        False,
    ),
    ("Pantry Staples", "Şəkər", [""], [(1000, "g"), (5000, "g")], (130, 700), False),
    (
        "Pantry Staples",
        "Makaron",
        ["Spagetti", "Qələmcə"],
        [(400, "g"), (800, "g")],
        (90, 320),
        False,
    ),
    ("Pantry Staples", "Qarabaşaq", [""], [(500, "g"), (800, "g")], (190, 480), False),
    ("Meat & Poultry", "Toyuq budu", [""], [(None, "kg_bulk")], (520, 780), True),
    ("Meat & Poultry", "Toyuq sinəsi", [""], [(None, "kg_bulk")], (640, 980), True),
    ("Meat & Poultry", "Mal əti", ["Biqqa", "Sümüksüz"], [(None, "kg_bulk")], (1350, 1950), True),
    ("Meat & Poultry", "Qiymə", [""], [(None, "kg_bulk")], (1050, 1550), True),
    ("Fruit & Vegetables", "Pomidor", ["Çəhrayı", "Yerli"], [(None, "kg_bulk")], (120, 380), True),
    ("Fruit & Vegetables", "Xiyar", ["Yerli"], [(None, "kg_bulk")], (80, 300), True),
    ("Fruit & Vegetables", "Kartof", ["Yerli"], [(None, "kg_bulk")], (70, 160), True),
    ("Fruit & Vegetables", "Soğan", ["Sarı"], [(None, "kg_bulk")], (50, 130), True),
    ("Fruit & Vegetables", "Alma", ["Qırmızı", "Yaşıl"], [(None, "kg_bulk")], (110, 340), True),
    ("Fruit & Vegetables", "Banan", [""], [(None, "kg_bulk")], (180, 320), True),
    ("Fruit & Vegetables", "Portağal", [""], [(None, "kg_bulk")], (150, 330), True),
    (
        "Household",
        "Yuyucu toz",
        ["Avtomat"],
        [(1500, "g"), (3000, "g"), (6000, "g")],
        (680, 2900),
        False,
    ),
    ("Household", "Qab yuyan gel", ["Limon"], [(450, "ml"), (900, "ml")], (150, 520), False),
    ("Household", "Tualet kağızı", [""], [(8, "pcs"), (12, "pcs")], (240, 800), False),
    ("Household", "Zibil torbası", [""], [(30, "pcs"), (60, "pcs")], (90, 330), False),
    (
        "Personal Care",
        "Şampun",
        ["Gündəlik", "Kepəyə qarşı"],
        [(250, "ml"), (400, "ml")],
        (320, 1450),
        False,
    ),
    ("Personal Care", "Diş pastası", ["Nanə"], [(75, "ml"), (100, "ml")], (140, 640), False),
    ("Personal Care", "Sabun", [""], [(90, "g"), (125, "g")], (60, 240), False),
    (
        "Personal Care",
        "Duş geli",
        ["Okean", "Çiçək"],
        [(250, "ml"), (400, "ml")],
        (260, 980),
        False,
    ),
    ("Frozen", "Dondurma", ["Vanil", "Şokolad"], [(90, "ml"), (400, "ml")], (80, 620), False),
    ("Frozen", "Pelmeni", [""], [(400, "g"), (800, "g")], (350, 1150), False),
    ("Frozen", "Tərəvəz qarışığı", [""], [(400, "g"), (700, "g")], (190, 540), False),
]

# Monthly drift of the *general* price level per category (what the price index should recover).
CATEGORY_MONTHLY_DRIFT = {
    "Dairy & Eggs": 0.012,
    "Bakery": 0.010,
    "Beverages": 0.006,
    "Snacks & Sweets": 0.007,
    "Pantry Staples": 0.009,
    "Meat & Poultry": 0.011,
    "Fruit & Vegetables": 0.004,
    "Household": 0.003,
    "Personal Care": 0.004,
    "Frozen": 0.005,
}


@dataclass(frozen=True)
class Product:
    pid: int
    category: str
    type_name: str
    variant: str
    brand: str | None
    unit_value: int | None  # grams / ml / pieces; None for weighed goods
    unit_type: str  # g | ml | pcs | kg_bulk
    ean: str | None
    base_price: int  # qepik (per kg for weighed goods)
    pack: int | None = None
    tags: tuple = field(default_factory=tuple)

    @property
    def weighed(self) -> bool:
        return self.unit_type == "kg_bulk"

    @property
    def canonical_name(self) -> str:
        parts = [self.brand, self.type_name, self.variant, self.size_label()]
        return " ".join(p for p in parts if p)

    def size_label(self) -> str:
        if self.weighed:
            return "kq"
        v, u = self.unit_value, self.unit_type
        if u == "pcs":
            return f"{v} ədəd"
        if u == "ml":
            return f"{v / 1000:g} l" if v >= 1000 else f"{v} ml"
        return f"{v / 1000:g} kq" if v >= 1000 else f"{v} q"


def gtin13(body12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


def build_catalog(n_products: int, seed: int) -> list[Product]:
    """Deterministic catalogue of roughly ``n_products`` canonical products."""
    rng = random.Random(seed)
    combos: list[tuple] = []
    for category, type_name, variants, sizes, price_range, weighed in _T:
        for variant in variants:
            for size in sizes:
                combos.append((category, type_name, variant, size, price_range, weighed))
    rng.shuffle(combos)

    products: list[Product] = []
    used_eans: set[str] = set()
    pid = 1
    # Several brands per type/size make "near twins" (same type and size, different brand) and
    # variants (3.2% vs 1.5%) make hard negatives for the matcher.
    for _round in range(200):
        if len(products) >= n_products:
            break
        for category, type_name, variant, (value, unit), (lo, hi), weighed in combos:
            if len(products) >= n_products:
                break
            brand = None if weighed else rng.choice(BRANDS)
            if any(
                p
                for p in products
                if (p.category, p.type_name, p.variant, p.brand, p.unit_value)
                == (category, type_name, variant, brand, value)
            ):
                continue
            base = int(round(rng.uniform(lo, hi)))
            base = max(30, base // 5 * 5)
            pack = (
                value if unit == "pcs" and type_name in ("Tualet kağızı", "Zibil torbası") else None
            )
            ean = None
            if not weighed:
                while True:
                    ean = gtin13("476" + "".join(rng.choice("0123456789") for _ in range(9)))
                    if ean not in used_eans:
                        used_eans.add(ean)
                        break
            products.append(
                Product(pid, category, type_name, variant, brand, value, unit, ean, base, pack)
            )
            pid += 1
    return products

"""Cross-retailer product matching with confidence scores and a human review queue.

Cascade, strongest evidence first:

1. ``ean``               same global barcode                         confidence 1.00  auto
2. ``fingerprint``       identical name fingerprint (tokens + size)  confidence 0.95  auto
3. ``loose_fingerprint`` same, tolerant to a space inside the brand  confidence 0.92  auto
4. ``fuzzy``             blocked on size, scored on name/brand       confidence = score
                         >= auto threshold and clear margin -> auto
                         >= review threshold                -> review queue
                         otherwise                          -> new canonical product

Rules that come from the domain and are not tunable:

* A different pack size is a different product. Size is a hard gate, never a weighted feature.
* Fat percentage and numeric variants (batteries 2025 vs 2032) must agree when both are present.
* Two different SKUs of the *same* retailer are not merged by name evidence: identical names inside one
  chain almost always mean different articles. Only a shared barcode may do that.
* Weighed goods (``kg_bulk``) only ever match weighed goods.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg
from rapidfuzz import fuzz

from rpi import logging_setup
from rpi.config import Settings, get_settings
from rpi.parsing import fingerprint

log = logging_setup.get(__name__)

_TURKIC_LOWER = str.maketrans({"I": "ı", "İ": "i"})


def display_name(raw: str) -> str:
    """Readable canonical name: ``XƏZƏR SÜD 3.2% 1L`` -> ``Xəzər Süd 3.2% 1l``."""
    text = raw.strip()
    if text.isupper():
        text = text.translate(_TURKIC_LOWER).lower()
    return " ".join(w[:1].upper() + w[1:] if not w[:1].isdigit() else w for w in text.split())


@dataclass
class Item:
    id: int
    retailer: str
    name_raw: str
    brand: str | None
    unit_type: str | None
    unit_value: float | None
    pack: int | None
    ean: str | None
    ean_kind: str
    category: str
    key: tuple | None = None
    loose: tuple | None = None
    tokens: str = ""

    def __post_init__(self) -> None:
        self.key = fingerprint.key(self.name_raw)
        self.loose = fingerprint.loose_key(self.name_raw)
        self.tokens = " ".join(sorted(fingerprint.tokens(self.name_raw)))

    @property
    def fat(self) -> float | None:
        return fingerprint.fat(self.name_raw)

    @property
    def variants(self) -> tuple:
        return fingerprint.variant_digits(self.name_raw)


@dataclass
class Canon:
    id: int
    category: str
    brand: str | None
    unit_type: str | None
    unit_value: float | None
    pack: int | None
    tokens: str
    fat: float | None
    variants: tuple
    retailers: set[str] = field(default_factory=set)


def _block_key(unit_type, unit_value, pack) -> tuple:
    return (unit_type, None if unit_value is None else round(float(unit_value), 1), pack)


def _brand_score(a: str | None, b: str | None) -> float | None:
    """1.0 same, 0.6 unknown on a side, None = different brands (hard negative)."""
    if not a or not b:
        return 0.6
    return 1.0 if fuzz.ratio(a, b) >= 85 else None


def score_pair(item: Item, c: Canon) -> tuple[float, dict] | None:
    """Score an item against a canonical product, or None when a hard rule excludes the pair."""
    if (item.unit_type, item.pack) != (c.unit_type, c.pack):
        return None
    if item.unit_type != "kg_bulk" and (item.unit_value or 0) != (c.unit_value or 0):
        return None
    if item.fat is not None and c.fat is not None and item.fat != c.fat:
        return None
    if item.variants != c.variants and item.variants and c.variants:
        return None
    brand = _brand_score(item.brand, c.brand)
    if brand is None:
        return None
    name = fuzz.token_sort_ratio(item.tokens, c.tokens) / 100.0
    category = 1.0 if item.category == c.category else 0.0
    score = round(0.60 * name + 0.25 * brand + 0.15 * category, 3)
    return score, {
        "name_similarity": round(name, 3),
        "brand_score": brand,
        "category_match": bool(category),
    }


@dataclass
class MatchStats:
    ean: int = 0
    fingerprint: int = 0
    loose_fingerprint: int = 0
    fuzzy_auto: int = 0
    review: int = 0
    new: int = 0

    def total(self) -> int:
        return sum(vars(self).values())


class Matcher:
    def __init__(self, conn: psycopg.Connection, settings: Settings | None = None) -> None:
        self.conn = conn
        self.s = settings or get_settings()
        self.by_ean: dict[str, int] = {}
        self.by_key: dict[tuple, int] = {}
        self.by_loose: dict[tuple, int] = {}
        self.blocks: dict[tuple, list[int]] = defaultdict(list)
        self.canon: dict[int, Canon] = {}
        self.stats = MatchStats()

    # -- registry ---------------------------------------------------------------------------

    def _load_registry(self) -> None:
        rows = self.conn.execute(
            """SELECT m.product_id, m.status, si.* FROM silver.product_match m
               JOIN silver.store_item si ON si.id = m.store_item_id
               WHERE m.status <> 'pending_review' ORDER BY m.matched_at, si.id"""
        ).fetchall()
        for r in rows:
            item = self._item(r)
            if r["product_id"] not in self.canon:
                self._register(r["product_id"], item)
            self._attach(r["product_id"], item)

    @staticmethod
    def _item(r: dict) -> Item:
        return Item(
            r["id"],
            r["retailer_code"],
            r["name_raw"],
            r["brand"],
            r["unit_type"],
            None if r["unit_value"] is None else float(r["unit_value"]),
            r["pack"],
            r["ean"],
            r["ean_kind"],
            r["category"],
        )

    def _register(self, pid: int, item: Item) -> None:
        self.canon[pid] = Canon(
            pid,
            item.category,
            item.brand,
            item.unit_type,
            item.unit_value,
            item.pack,
            item.tokens,
            item.fat,
            item.variants,
        )
        self.blocks[_block_key(item.unit_type, item.unit_value, item.pack)].append(pid)

    def _attach(self, pid: int, item: Item) -> None:
        c = self.canon[pid]
        c.retailers.add(item.retailer)
        if item.ean_kind == "global" and item.ean:
            self.by_ean.setdefault(item.ean, pid)
        if item.key:
            self.by_key.setdefault(item.key, pid)
        if item.loose:
            self.by_loose.setdefault(item.loose, pid)
        if c.brand is None and item.brand:
            c.brand = item.brand

    # -- decisions --------------------------------------------------------------------------

    def _new_product(self, item: Item) -> int:
        pid = self.conn.execute(
            """INSERT INTO silver.product (name, brand, unit_value, unit_type, pack, category, ean, is_weighed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                display_name(item.name_raw),
                item.brand,
                item.unit_value,
                item.unit_type,
                item.pack,
                item.category,
                item.ean if item.ean_kind == "global" else None,
                item.unit_type == "kg_bulk",
            ),
        ).fetchone()["id"]
        self._register(pid, item)
        return pid

    def _save(self, item: Item, pid: int, method: str, conf: float, status: str) -> None:
        self.conn.execute(
            """INSERT INTO silver.product_match (store_item_id, product_id, method, confidence, status)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (store_item_id) DO NOTHING""",
            (item.id, pid, method, conf, status),
        )
        self._attach(pid, item)

    def _fuzzy_candidates(self, item: Item) -> list[tuple[float, int, dict]]:
        out = []
        for pid in self.blocks.get(_block_key(item.unit_type, item.unit_value, item.pack), []):
            c = self.canon[pid]
            if item.retailer in c.retailers:
                continue
            scored = score_pair(item, c)
            if scored:
                out.append((scored[0], pid, scored[1]))
        return sorted(out, key=lambda t: (-t[0], t[1]))

    def match_item(self, item: Item) -> str:
        s = self.s
        if item.ean_kind == "global" and item.ean in self.by_ean:
            pid = self.by_ean[item.ean]
            self._save(item, pid, "ean", 1.0, "auto")
            self.stats.ean += 1
            return "ean"
        if (
            item.key
            and item.key in self.by_key
            and item.retailer not in self.canon[self.by_key[item.key]].retailers
        ):
            self._save(item, self.by_key[item.key], "fingerprint", 0.95, "auto")
            self.stats.fingerprint += 1
            return "fingerprint"
        if (
            item.loose
            and item.loose in self.by_loose
            and item.retailer not in self.canon[self.by_loose[item.loose]].retailers
        ):
            self._save(item, self.by_loose[item.loose], "loose_fingerprint", 0.92, "auto")
            self.stats.loose_fingerprint += 1
            return "loose_fingerprint"

        cands = self._fuzzy_candidates(item)
        best = cands[0] if cands else None
        if (
            best
            and best[0] >= s.match_auto_threshold
            and (len(cands) == 1 or best[0] - cands[1][0] >= 0.04)
        ):
            self._save(item, best[1], "fuzzy", best[0], "auto")
            self.stats.fuzzy_auto += 1
            return "fuzzy"
        pid = self._new_product(item)
        if best and best[0] >= s.match_review_threshold:
            self._save(item, pid, "review", best[0], "pending_review")
            for score, cand_pid, feats in [c for c in cands if c[0] >= s.match_review_threshold][
                :3
            ]:
                self.conn.execute(
                    """INSERT INTO silver.match_review (store_item_id, candidate_product_id, score, features)
                       VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT (store_item_id, candidate_product_id) DO NOTHING""",
                    (item.id, cand_pid, score, json.dumps(feats)),
                )
            self.stats.review += 1
            return "review"
        self._save(item, pid, "new", 1.0, "singleton")
        self.stats.new += 1
        return "new"

    def run(self) -> MatchStats:
        """Match every store item that has no match yet. Safe to re-run: matched items are untouched."""
        self._load_registry()
        rows = self.conn.execute(
            """SELECT si.* FROM silver.store_item si LEFT JOIN silver.product_match m ON m.store_item_id = si.id
               WHERE m.store_item_id IS NULL
               ORDER BY (si.ean_kind <> 'global'), si.retailer_code, si.id"""
        ).fetchall()
        for r in rows:
            self.match_item(self._item(r))
        log.info("matching finished", extra={"items": len(rows), **vars(self.stats)})
        return self.stats


# ------------------------------------------------------------------------------ review actions


def approve_review(conn: psycopg.Connection, review_id: int, decided_by: str = "reviewer") -> dict:
    r = conn.execute(
        "SELECT * FROM silver.match_review WHERE id = %s AND status = 'pending'", (review_id,)
    ).fetchone()
    if not r:
        raise LookupError(f"no pending review {review_id}")
    old = conn.execute(
        "SELECT product_id FROM silver.product_match WHERE store_item_id = %s",
        (r["store_item_id"],),
    ).fetchone()
    conn.execute(
        """UPDATE silver.product_match SET product_id = %s, method = 'review', status = 'approved', matched_at = now()
           WHERE store_item_id = %s""",
        (r["candidate_product_id"], r["store_item_id"]),
    )
    conn.execute(
        "UPDATE silver.match_review SET status = 'approved', decided_at = now(), decided_by = %s WHERE id = %s",
        (decided_by, review_id),
    )
    conn.execute(
        """UPDATE silver.match_review SET status = 'rejected', decided_at = now(), decided_by = %s
           WHERE store_item_id = %s AND status = 'pending'""",
        (f"{decided_by}:superseded", r["store_item_id"]),
    )
    if old and old["product_id"] != r["candidate_product_id"]:
        conn.execute(
            """DELETE FROM silver.product p WHERE p.id = %s
               AND NOT EXISTS (SELECT 1 FROM silver.product_match m WHERE m.product_id = p.id)""",
            (old["product_id"],),
        )
    return {
        "review_id": review_id,
        "store_item_id": r["store_item_id"],
        "product_id": r["candidate_product_id"],
    }


def reject_review(conn: psycopg.Connection, review_id: int, decided_by: str = "reviewer") -> dict:
    r = conn.execute(
        "SELECT * FROM silver.match_review WHERE id = %s AND status = 'pending'", (review_id,)
    ).fetchone()
    if not r:
        raise LookupError(f"no pending review {review_id}")
    conn.execute(
        "UPDATE silver.match_review SET status = 'rejected', decided_at = now(), decided_by = %s WHERE id = %s",
        (decided_by, review_id),
    )
    left = conn.execute(
        "SELECT count(*) AS n FROM silver.match_review WHERE store_item_id = %s AND status = 'pending'",
        (r["store_item_id"],),
    ).fetchone()["n"]
    if left == 0:  # every candidate refused: this item is its own product
        conn.execute(
            "UPDATE silver.product_match SET status = 'singleton', method = 'new', confidence = 1 "
            "WHERE store_item_id = %s AND status = 'pending_review'",
            (r["store_item_id"],),
        )
    return {
        "review_id": review_id,
        "store_item_id": r["store_item_id"],
        "remaining_candidates": left,
    }


# ------------------------------------------------------------------------------ quarantine


def refresh_quarantine(conn: psycopg.Connection, *, max_price_ratio: float = 3.0) -> dict:
    """Flag merges that look wrong. Quarantined products never reach the gold layer.

    size_conflict   members disagree on pack size / unit (usually a mistyped barcode)
    ean_conflict    members carry different global barcodes
    price_spread    typical regular price differs by more than ``max_price_ratio`` between members
    """
    conn.execute(
        "UPDATE silver.product SET quarantined = false, quarantine_reason = NULL WHERE quarantined"
    )
    flagged: dict[str, int] = {}
    queries = {
        "size_conflict": """
            SELECT m.product_id FROM silver.product_match m JOIN silver.store_item si ON si.id = m.store_item_id
            WHERE si.unit_type <> 'kg_bulk' OR si.unit_type IS NULL
            GROUP BY m.product_id
            HAVING count(DISTINCT (si.unit_type, COALESCE(si.unit_value, 0), COALESCE(si.pack, 0))) > 1""",
        "ean_conflict": """
            SELECT m.product_id FROM silver.product_match m JOIN silver.store_item si ON si.id = m.store_item_id
            WHERE si.ean_kind = 'global' GROUP BY m.product_id HAVING count(DISTINCT si.ean) > 1""",
        "price_spread": f"""
            WITH last AS (SELECT max(observed_date) d FROM silver.price_observation),
            typical AS (
                SELECT o.store_item_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY o.price_qepik) AS p
                FROM silver.price_observation o, last
                WHERE o.observed_date > last.d - 7 AND o.old_price_qepik IS NULL GROUP BY o.store_item_id)
            SELECT m.product_id FROM silver.product_match m JOIN typical t ON t.store_item_id = m.store_item_id
            GROUP BY m.product_id HAVING count(*) > 1 AND max(t.p) / NULLIF(min(t.p), 0) > {max_price_ratio}""",
    }
    for reason, sql in queries.items():
        ids = [r["product_id"] for r in conn.execute(sql)]
        if ids:
            conn.execute(
                "UPDATE silver.product SET quarantined = true, quarantine_reason = %s "
                "WHERE id = ANY(%s) AND NOT quarantined",
                (reason, ids),
            )
        flagged[reason] = len(ids)
    return flagged

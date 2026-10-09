"""Bronze -> silver.

For each bronze batch: parse with the source's parser, validate row by row, drop duplicates, gate the
batch as a whole (a feed that is wrong everywhere must not be half-loaded), enrich items (units,
brand, EAN kind, category) and append price observations.

Invariant checked by tests: rows in the batch == observations inserted + observations that already
existed + rejected rows. Nothing disappears silently.
"""

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime
from pathlib import Path

import polars as pl
import psycopg

from rpi import logging_setup, ops
from rpi.config import Settings, get_settings
from rpi.parsing import fingerprint, units
from rpi.silver.parsers import BAKU, parse_record
from rpi.text import normalize_text, valid_gtin

log = logging_setup.get(__name__)
REFERENCE = Path(__file__).resolve().parent.parent / "reference"

_SCHEMA = {
    "row_num": pl.Int64,
    "sku": pl.Utf8,
    "name": pl.Utf8,
    "brand": pl.Utf8,
    "ean": pl.Utf8,
    "category_raw": pl.Utf8,
    "price": pl.Int64,
    "old_price": pl.Int64,
    "available": pl.Boolean,
    "store_code": pl.Utf8,
    "observed_at": pl.Datetime("us", "Asia/Baku"),
    "promo_until": pl.Date,
    "error": pl.Utf8,
}


def ensure_reference(conn: psycopg.Connection) -> None:
    """Load the retailer registry (idempotent)."""
    with open(REFERENCE / "retailers.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            conn.execute(
                """INSERT INTO silver.retailer (code, name, price_model) VALUES (%s, %s, %s)
                   ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, price_model = EXCLUDED.price_model""",
                (r["code"], r["name"], r["price_model"]),
            )


def category_map() -> dict[tuple[str, str], str]:
    with open(REFERENCE / "category_map.csv", encoding="utf-8") as f:
        return {(r["retailer_code"], r["raw_category"]): r["category"] for r in csv.DictReader(f)}


# ------------------------------------------------------------------------------ enrichment


def classify_ean(ean: str | None) -> tuple[str | None, str]:
    """Return (ean, kind). In-store codes (prefix 2) and bad check digits are not global barcodes."""
    if not ean:
        return None, "none"
    digits = ean.strip()
    if valid_gtin(digits) and not digits.startswith("2"):
        return digits, "global"
    return digits, "internal"


def enrich_item(
    name: str,
    brand_raw: str | None,
    category_raw: str | None,
    retailer: str,
    brands: set[str],
    cmap: dict[tuple[str, str], str],
) -> dict:
    value, unit, pack = units.parse(name)
    name_norm = normalize_text(name)
    brand = normalize_text(brand_raw) or None
    if brand is None:  # try to recover the brand from a lexicon built from other rows
        padded = f" {name_norm} "
        for candidate in sorted(brands, key=len, reverse=True):
            if f" {candidate} " in padded:
                brand = candidate
                break
    top = (category_raw or "").split(">")[0].strip()
    return {
        "name_norm": name_norm,
        "brand": brand,
        "unit_value": None if value is None else round(float(value), 3),
        "unit_type": unit,
        "pack": pack,
        "category": cmap.get((retailer, top), "Uncategorized"),
        "fingerprint": repr(fingerprint.key(name)),
    }


# ------------------------------------------------------------------------------ batch gates


def _reject_batch(
    conn: psycopg.Connection, batch: dict, run_id: str | None, reason: str, detail: str
) -> dict:
    conn.execute(
        "UPDATE bronze.ingest_batch SET status = 'rejected', status_detail = %s, silver_at = now() WHERE batch_id = %s",
        (f"{reason}: {detail}", batch["batch_id"]),
    )
    ops.raise_alert(
        conn,
        run_id=run_id,
        severity="error",
        source=batch["source"],
        title=f"batch rejected: {reason}",
        detail=f"{Path(batch['landing_path']).name} - {detail}",
    )
    log.error(
        "batch rejected",
        extra={
            "source": batch["source"],
            "batch_id": batch["batch_id"],
            "reason": reason,
            "detail": detail,
        },
    )
    return {
        "batch_id": batch["batch_id"],
        "source": batch["source"],
        "status": "rejected",
        "reason": reason,
    }


# ------------------------------------------------------------------------------ prices


def process_prices_batch(
    conn: psycopg.Connection,
    batch: dict,
    *,
    run_id: str | None = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    source, batch_id = batch["source"], batch["batch_id"]
    retailer = conn.execute(
        "SELECT code, price_model FROM silver.retailer WHERE code = %s", (source,)
    ).fetchone()
    if not retailer:
        return _reject_batch(
            conn, batch, run_id, "unknown_source", f"{source} is not a registered retailer"
        )

    raw = conn.execute(
        "SELECT row_num, payload FROM bronze.raw_record WHERE batch_id = %s ORDER BY row_num",
        (batch_id,),
    ).fetchall()
    payloads = {r["row_num"]: r["payload"] for r in raw}

    records = []
    for r in raw:
        p = parse_record(source, r["payload"])
        records.append(
            {
                "row_num": r["row_num"],
                "sku": p.sku,
                "name": p.name,
                "brand": p.brand,
                "ean": p.ean,
                "category_raw": p.category_raw,
                "price": p.price,
                "old_price": p.old_price,
                "available": p.available,
                "store_code": p.store_code,
                "observed_at": p.observed_at,
                "promo_until": p.promo_until,
                "error": p.error,
            }
        )
    df = pl.DataFrame(records, schema=_SCHEMA)

    stores = {
        r["store_code"]: r["id"]
        for r in conn.execute(
            "SELECT id, store_code FROM silver.store WHERE retailer_code = %s", (source,)
        )
    }
    zoned = retailer["price_model"] == "zoned"

    unknown_store = (
        ~pl.col("store_code").is_in(list(stores)).fill_null(False) if zoned else pl.lit(False)
    )
    df = df.with_columns(
        pl.when(pl.col("error").is_not_null())
        .then(pl.col("error"))
        .when(pl.col("sku").is_null())
        .then(pl.lit("missing_sku"))
        .when(pl.col("name").is_null())
        .then(pl.lit("missing_name"))
        .when(pl.col("price").is_null())
        .then(pl.lit("invalid_price"))
        .when(pl.col("price") <= 0)
        .then(pl.lit("nonpositive_price"))
        .when(unknown_store)
        .then(pl.lit("unknown_store"))
        .otherwise(None)
        .alias("reason")
    ).with_columns(
        # "was" price that is not above the current price is not a promotion; keep the price, drop the claim.
        pl.when(pl.col("old_price") <= pl.col("price"))
        .then(None)
        .otherwise(pl.col("old_price"))
        .alias("old_price")
    )

    valid = df.filter(pl.col("reason").is_null())
    key = ["sku", "store_code"]
    kept = valid.unique(subset=key, keep="first", maintain_order=True)
    dupes = valid.join(kept.select("row_num"), on="row_num", how="anti")
    first_price = kept.select(key + [pl.col("price").alias("kept_price")])
    dupes = dupes.join(first_price, on=key, how="left", nulls_equal=True).with_columns(
        pl.when(pl.col("price") == pl.col("kept_price"))
        .then(pl.lit("duplicate_row"))
        .otherwise(pl.lit("conflicting_duplicate"))
        .alias("reason")
    )
    invalid = df.filter(pl.col("reason").is_not_null())
    n_rows = df.height

    # ---- batch gates -------------------------------------------------------------------------
    bad = invalid.height / n_rows if n_rows else 1.0
    if n_rows == 0:
        return _reject_batch(conn, batch, run_id, "empty_batch", "file has no records")
    if bad > 0.20:
        return _reject_batch(
            conn, batch, run_id, "reject_ratio", f"{bad:.0%} of rows are invalid (limit 20%)"
        )
    median = statistics.median(kept["price"].to_list()) if kept.height else 0
    if not settings.median_price_min <= median <= settings.median_price_max:
        return _reject_batch(
            conn,
            batch,
            run_id,
            "median_price_out_of_range",
            f"median price {median / 100:.2f} AZN outside {settings.median_price_min / 100:.2f}-"
            f"{settings.median_price_max / 100:.2f} AZN",
        )
    previous = [
        r["row_count"]
        for r in conn.execute(
            """SELECT row_count FROM bronze.ingest_batch WHERE source = %s AND kind = 'prices' AND status = 'silver_done'
           AND business_date < %s ORDER BY business_date DESC LIMIT 7""",
            (source, batch["business_date"]),
        )
    ]
    if len(previous) >= 3 and n_rows < 0.5 * statistics.median(previous):
        return _reject_batch(
            conn,
            batch,
            run_id,
            "row_count_collapse",
            f"{n_rows} rows vs trailing median {statistics.median(previous):.0f}",
        )

    # ---- items -------------------------------------------------------------------------------
    brands = {
        r["brand"]
        for r in conn.execute(
            "SELECT DISTINCT brand FROM silver.store_item WHERE brand IS NOT NULL"
        )
    }
    brands |= {normalize_text(b) for b in kept["brand"].drop_nulls().unique().to_list()}
    cmap = category_map()
    business_date = batch["business_date"]
    item_rows = []
    for row in kept.unique(subset="sku", keep="first", maintain_order=True).iter_rows(named=True):
        e = enrich_item(row["name"], row["brand"], row["category_raw"], source, brands, cmap)
        ean, kind = classify_ean(row["ean"])
        item_rows.append(
            (
                source,
                row["sku"],
                row["name"],
                e["name_norm"],
                e["brand"],
                e["unit_value"],
                e["unit_type"],
                e["pack"],
                ean,
                kind,
                row["category_raw"],
                e["category"],
                business_date,
                business_date,
            )
        )
    conn.execute(
        """CREATE TEMP TABLE _items (retailer_code text, sku text, name_raw text, name_norm text, brand text,
           unit_value numeric, unit_type text, pack int, ean text, ean_kind text, category_raw text,
           category text, first_seen date, last_seen date) ON COMMIT DROP"""
    )
    with conn.cursor() as cur, cur.copy("COPY _items FROM STDIN") as cp:
        for row in item_rows:
            cp.write_row(row)
    ids = {
        r["sku"]: r["id"]
        for r in conn.execute(
            """INSERT INTO silver.store_item AS si (retailer_code, sku, name_raw, name_norm, brand, unit_value, unit_type,
               pack, ean, ean_kind, category_raw, category, first_seen, last_seen)
           SELECT * FROM _items
           ON CONFLICT (retailer_code, sku) DO UPDATE SET
               first_seen = LEAST(si.first_seen, EXCLUDED.first_seen),
               last_seen  = GREATEST(si.last_seen, EXCLUDED.last_seen),
               brand      = COALESCE(si.brand, EXCLUDED.brand),
               ean        = COALESCE(si.ean, EXCLUDED.ean),
               ean_kind   = CASE WHEN si.ean_kind = 'none' THEN EXCLUDED.ean_kind ELSE si.ean_kind END
           RETURNING id, sku"""
        )
    }
    conn.execute("DROP TABLE _items")

    # ---- observations (append-only) ----------------------------------------------------------
    conn.execute(
        """CREATE TEMP TABLE _obs (store_item_id bigint, store_id bigint, observed_date date, observed_at timestamptz,
           price_qepik int, old_price_qepik int, available boolean, promo_until date, batch_id bigint) ON COMMIT DROP"""
    )
    with conn.cursor() as cur, cur.copy("COPY _obs FROM STDIN") as cp:
        for row in kept.iter_rows(named=True):
            at = row["observed_at"] or datetime.combine(
                business_date, datetime.min.time(), tzinfo=BAKU
            )
            cp.write_row(
                (
                    ids[row["sku"]],
                    stores.get(row["store_code"]) if zoned else None,
                    business_date,
                    at,
                    row["price"],
                    row["old_price"],
                    row["available"],
                    row["promo_until"],
                    batch_id,
                )
            )
    inserted = conn.execute(
        """INSERT INTO silver.price_observation (store_item_id, store_id, observed_date, observed_at, price_qepik,
               old_price_qepik, available, promo_until, batch_id)
           SELECT * FROM _obs
           ON CONFLICT (store_item_id, COALESCE(store_id, 0), observed_date) DO NOTHING"""
    ).rowcount
    conn.execute("DROP TABLE _obs")
    already = kept.height - inserted

    # ---- rejected rows -----------------------------------------------------------------------
    rejected = pl.concat(
        [
            invalid.select("row_num", "reason", "name"),
            dupes.select("row_num", "reason", "name"),
        ]
    )
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO silver.rejected_record (batch_id, row_num, source, reason, detail, payload)
               VALUES (%s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (batch_id, row_num) DO NOTHING""",
            [
                (
                    batch_id,
                    r["row_num"],
                    source,
                    r["reason"],
                    (r["name"] or "")[:120],
                    json.dumps(payloads[r["row_num"]], ensure_ascii=False),
                )
                for r in rejected.iter_rows(named=True)
            ],
        )
    conn.execute(
        "UPDATE bronze.ingest_batch SET status = 'silver_done', silver_at = now() WHERE batch_id = %s",
        (batch_id,),
    )

    reasons = rejected.group_by("reason").len().to_dicts()
    stats = {
        "batch_id": batch_id,
        "source": source,
        "status": "silver_done",
        "rows_in": n_rows,
        "observations_inserted": inserted,
        "observations_already_present": already,
        "rejected": rejected.height,
        "rejected_by_reason": {r["reason"]: r["len"] for r in reasons},
        "median_price_qepik": median,
    }
    log.info("silver batch processed", extra=stats)
    return stats


# ------------------------------------------------------------------------------ stores


def process_stores_batch(conn: psycopg.Connection, batch: dict) -> dict:
    rows = conn.execute(
        "SELECT payload FROM bronze.raw_record WHERE batch_id = %s ORDER BY row_num",
        (batch["batch_id"],),
    ).fetchall()
    n = 0
    for r in rows:
        p = r["payload"]
        if not p.get("store_code") or not p.get("price_zone"):
            continue
        conn.execute(
            """INSERT INTO silver.store (retailer_code, store_code, name, format, price_zone)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (retailer_code, store_code) DO UPDATE
               SET name = EXCLUDED.name, format = EXCLUDED.format, price_zone = EXCLUDED.price_zone""",
            (
                batch["source"],
                p["store_code"],
                p.get("name") or p["store_code"],
                p.get("format"),
                p["price_zone"],
            ),
        )
        n += 1
    conn.execute(
        "UPDATE bronze.ingest_batch SET status = 'silver_done', silver_at = now() WHERE batch_id = %s",
        (batch["batch_id"],),
    )
    return {
        "batch_id": batch["batch_id"],
        "source": batch["source"],
        "status": "silver_done",
        "stores": n,
    }


def process_pending(
    conn: psycopg.Connection, *, run_id: str | None = None, settings: Settings | None = None
) -> list[dict]:
    """Process every bronze batch not yet in silver, oldest first, stores before prices.

    One transaction per batch: a failing batch rolls back alone and is marked failed, so a re-run
    resumes exactly where the previous one stopped.
    """
    ensure_reference(conn)
    conn.commit()
    pending = conn.execute(
        """SELECT * FROM bronze.ingest_batch WHERE status = 'ingested'
           ORDER BY (kind <> 'stores'), business_date, batch_id"""
    ).fetchall()
    results = []
    for batch in pending:
        try:
            with conn.transaction():
                if batch["kind"] == "stores":
                    results.append(process_stores_batch(conn, batch))
                else:
                    results.append(
                        process_prices_batch(conn, batch, run_id=run_id, settings=settings)
                    )
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised after the loop continues
            conn.execute(
                "UPDATE bronze.ingest_batch SET status = 'failed', status_detail = %s WHERE batch_id = %s",
                (f"{type(exc).__name__}: {exc}"[:500], batch["batch_id"]),
            )
            ops.raise_alert(
                conn,
                run_id=run_id,
                severity="error",
                source=batch["source"],
                title="silver processing failed",
                detail=f"{type(exc).__name__}: {exc}"[:500],
            )
            log.exception(
                "silver batch failed",
                extra={"batch_id": batch["batch_id"], "source": batch["source"]},
            )
            results.append(
                {"batch_id": batch["batch_id"], "source": batch["source"], "status": "failed"}
            )
        conn.commit()
    return results

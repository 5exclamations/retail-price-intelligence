"""Read-only queries for the dashboard. Everything comes from the gold layer (plus ops telemetry)."""

from __future__ import annotations

import polars as pl
import psycopg
from psycopg.adapt import Loader
from psycopg.rows import dict_row

from rpi.config import get_settings


class _Float(Loader):
    def load(self, data):
        return float(bytes(data))


def query(sql: str, params: tuple | list | None = None) -> pl.DataFrame:
    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as conn:
        conn.adapters.register_loader("numeric", _Float)
        rows = conn.execute(sql, params).fetchall()
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def azn(qepik: int | float | None) -> str:
    """Format integer qepik for display. The only place money becomes a decimal string."""
    if qepik is None:
        return "-"
    q = int(qepik)
    sign = "-" if q < 0 else ""
    return f"{sign}{abs(q) // 100}.{abs(q) % 100:02d} AZN"


def bp(value: int | float | None) -> str:
    return "-" if value is None else f"{value / 100:.1f}%"


def meta() -> dict:
    df = query("SELECT max(observed_date) AS d, max(observed_at) AS at FROM gold.fct_price_daily")
    return (
        {"as_of": df["d"][0], "last_observed_at": df["at"][0]} if df.height and df["d"][0] else {}
    )


def price_points() -> pl.DataFrame:
    return query(
        "SELECT price_point_key, retailer_code, retailer_name, price_zone, label, requires_zone_choice FROM gold.dim_price_point ORDER BY 1"
    )


def zoned_choices() -> dict[str, list[str]]:
    pp = price_points().filter(pl.col("requires_zone_choice"))
    out: dict[str, list[str]] = {}
    for r in pp.iter_rows(named=True):
        out.setdefault(r["retailer_code"], []).append(r["price_zone"])
    return out


def allowed_keys(selected: dict[str, str]) -> list[str]:
    pp = price_points()
    keys = []
    for r in pp.iter_rows(named=True):
        if not r["requires_zone_choice"] or selected.get(r["retailer_code"]) == r["price_zone"]:
            keys.append(r["price_point_key"])
    return keys


def products(
    search: str = "", category: str | None = None, min_retailers: int = 3, limit: int = 500
) -> pl.DataFrame:
    where, params = ["retailer_count >= %s"], [min_retailers]
    if search:
        where.append("lower(product_name) LIKE %s")
        params.append(f"%{search.lower()}%")
    if category and category != "All":
        where.append("category = %s")
        params.append(category)
    return query(
        f"SELECT product_key, product_name, category, retailer_count FROM gold.dim_product WHERE {' AND '.join(where)} "
        "ORDER BY retailer_count DESC, product_name LIMIT %s",
        [*params, limit],
    )


def categories() -> list[str]:
    return query("SELECT category FROM gold.dim_category ORDER BY 1")["category"].to_list()


def current_prices(product_key: int, keys: list[str]) -> pl.DataFrame:
    return query(
        """SELECT c.price_point_key, pp.label, pp.retailer_code, min(c.price_qepik) AS price_qepik,
                  min(c.regular_price_qepik) AS regular_price_qepik, min(c.old_price_qepik) AS old_price_qepik,
                  bool_or(c.is_promo) AS is_promo, bool_or(c.available) AS available,
                  max(c.observed_at) AS observed_at, min(c.days_since_observed) AS days_since_observed
           FROM gold.mart_price_current c JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE c.product_key = %s AND c.price_point_key = ANY(%s) GROUP BY 1, 2, 3 ORDER BY min(c.price_qepik)""",
        (product_key, keys),
    )


def history(product_key: int, keys: list[str], days: int) -> pl.DataFrame:
    return query(
        """SELECT f.observed_date, f.price_point_key, pp.label, min(f.price_qepik) AS price_qepik,
                  min(f.regular_price_qepik) AS regular_price_qepik, bool_or(f.is_promo) AS is_promo
           FROM gold.fct_price_daily f JOIN gold.dim_store_item b USING (store_item_id)
           JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE b.product_key = %s AND f.price_point_key = ANY(%s)
             AND f.observed_date > (SELECT max(observed_date) FROM gold.fct_price_daily) - %s
           GROUP BY 1, 2, 3 ORDER BY 1""",
        (product_key, keys, days),
    )


def competitiveness(keys: list[str]) -> pl.DataFrame:
    return query(
        """SELECT c.week_start, c.price_point_key, pp.label, c.retailer_code, c.n_products, c.price_index_vs_market,
                  c.share_cheapest_pct, c.promo_share_pct
           FROM gold.mart_retailer_competitiveness c JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE c.price_point_key = ANY(%s) ORDER BY 1, 2""",
        (keys,),
    )


def promotions(keys: list[str], limit: int = 300) -> pl.DataFrame:
    return query(
        """SELECT p.product_name, a.category, pp.label, a.retailer_code, a.price_qepik, a.old_price_qepik,
                  a.market_ref_qepik, a.claimed_discount_bp, a.real_discount_bp, a.ref_reliable, a.inflated_flag,
                  a.fake_flag, a.observed_at
           FROM gold.mart_promo_analysis a JOIN gold.dim_product p ON p.product_key = a.product_key
           JOIN gold.dim_price_point pp ON pp.price_point_key = a.price_point_key
           WHERE a.observed_date = (SELECT max(observed_date) FROM gold.mart_promo_analysis)
             AND a.price_point_key = ANY(%s) AND a.available
           ORDER BY a.real_discount_bp DESC NULLS LAST LIMIT %s""",
        (keys, limit),
    )


def promo_summary() -> pl.DataFrame:
    return query(
        """SELECT retailer_code, sum(promo_price_days) AS promo_price_days,
                  (sum(inflated_share_pct * promo_price_days) / NULLIF(sum(promo_price_days), 0))::float8 AS inflated_share_pct,
                  (sum(fake_share_pct * promo_price_days) / NULLIF(sum(promo_price_days), 0))::float8 AS fake_share_pct,
                  avg(median_claimed_bp)::float8 AS median_claimed_bp, avg(median_real_bp)::float8 AS median_real_bp
           FROM gold.mart_promo_retailer_summary GROUP BY 1 ORDER BY 1"""
    )


def price_index() -> pl.DataFrame:
    return query(
        "SELECT week_start, category, index_regular, index_effective, wow_change_pct, n_items, promo_share_pct, median_real_bp "
        "FROM gold.mart_category_trend ORDER BY 1, 2"
    )


def movers(direction: str, limit: int = 15) -> pl.DataFrame:
    order = "DESC" if direction == "up" else "ASC"
    return query(
        f"SELECT product_name, category, retailer_code, price_28d_ago_qepik, price_now_qepik, change_bp "
        f"FROM gold.mart_price_movers ORDER BY change_bp {order}, product_key LIMIT %s",
        (limit,),
    )


def basket_latest(keys: list[str]) -> pl.DataFrame:
    return query(
        """SELECT b.price_point_key, pp.label, b.total_qepik, b.items_total, b.items_available, b.is_complete
           FROM gold.mart_cheapest_basket b JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE b.observed_date = (SELECT max(observed_date) FROM gold.mart_cheapest_basket) AND b.price_point_key = ANY(%s)
           ORDER BY b.is_complete DESC, b.total_qepik""",
        (keys,),
    )


def basket_series(keys: list[str]) -> pl.DataFrame:
    return query(
        """SELECT b.observed_date, pp.label, b.total_qepik FROM gold.mart_cheapest_basket b
           JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE b.is_complete AND b.price_point_key = ANY(%s) ORDER BY 1""",
        (keys,),
    )


def basket_items() -> pl.DataFrame:
    return query(
        "SELECT product_key, product_name, category, quantity FROM gold.basket_definition ORDER BY category, product_key"
    )


def custom_basket(product_ids: list[int], keys: list[str]) -> pl.DataFrame:
    return query(
        """SELECT c.product_key, c.price_point_key, pp.label, min(c.price_qepik) AS price_qepik
           FROM gold.mart_price_current c JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE c.product_key = ANY(%s) AND c.price_point_key = ANY(%s) AND c.available AND c.days_since_observed <= 3
           GROUP BY 1, 2, 3""",
        (product_ids, keys),
    )


def review_queue(limit: int = 30) -> pl.DataFrame:
    return query(
        """SELECT rv.id, rv.score, si.retailer_code, si.name_raw AS item_name, p.name AS candidate_name,
                  rv.features->>'name_similarity' AS name_similarity, rv.features->>'brand_score' AS brand_score
           FROM silver.match_review rv JOIN silver.store_item si ON si.id = rv.store_item_id
           JOIN silver.product p ON p.id = rv.candidate_product_id
           WHERE rv.status = 'pending' ORDER BY rv.score DESC, rv.id LIMIT %s""",
        (limit,),
    )


def match_methods() -> pl.DataFrame:
    return query(
        "SELECT method, status, count(*) AS items, round(avg(confidence)::numeric, 3) AS avg_confidence "
        "FROM silver.product_match GROUP BY 1, 2 ORDER BY 3 DESC"
    )


def quarantined() -> pl.DataFrame:
    return query(
        "SELECT id, name, quarantine_reason FROM silver.product WHERE quarantined ORDER BY id"
    )


def dq_latest() -> pl.DataFrame:
    return query(
        """SELECT layer, check_name, scope, severity, passed, observed, threshold, detail, checked_at FROM ops.dq_result
           WHERE run_id = (SELECT run_id FROM ops.pipeline_run ORDER BY started_at DESC LIMIT 1)
           ORDER BY passed, severity, layer, check_name"""
    )


def runs() -> pl.DataFrame:
    return query(
        "SELECT run_id, status, as_of_date, started_at, finished_at, "
        "extract(epoch FROM finished_at - started_at)::int AS seconds FROM ops.pipeline_run ORDER BY started_at DESC LIMIT 15"
    )


def alerts() -> pl.DataFrame:
    return query(
        "SELECT created_at, severity, source, title, detail FROM ops.alert ORDER BY id DESC LIMIT 15"
    )


def lineage() -> pl.DataFrame:
    """Row counts layer by layer: where did the rows go?"""
    return query(
        """SELECT 'bronze: raw price records' AS stage, count(*)::bigint AS n FROM bronze.raw_record r
           JOIN bronze.ingest_batch b USING (batch_id) WHERE b.kind = 'prices' AND b.status = 'silver_done'
           UNION ALL SELECT 'silver: rejected records', count(*) FROM silver.rejected_record
           UNION ALL SELECT 'silver: price observations', count(*) FROM silver.price_observation
           UNION ALL SELECT 'gold: daily prices (fact)', count(*) FROM gold.fct_price_daily"""
    )


def batch_status() -> pl.DataFrame:
    return query(
        "SELECT status, count(*) AS batches FROM bronze.ingest_batch GROUP BY 1 ORDER BY 1"
    )

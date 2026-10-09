-- Sample queries against the gold layer. Run with:
--   psql "$RPI_DATABASE_URL" -f docs/sample_queries.sql
-- Money columns are integer qepik (100 qepik = 1 AZN); discounts are integer basis points (100 bp = 1%).

-- 1. Where is a product cheapest right now? (zoned chain restricted to zone A, as the zone rule demands)
SELECT p.product_name,
       pp.label                           AS store,
       c.price_qepik / 100.0              AS price_azn,
       c.observed_at,
       rank() OVER (PARTITION BY c.product_key ORDER BY c.price_qepik) AS price_rank
FROM gold.mart_price_current c
JOIN gold.dim_product p     USING (product_key)
JOIN gold.dim_price_point pp USING (price_point_key)
WHERE p.product_name = 'Alma Qırmızı Kq'
  AND c.available AND c.days_since_observed <= 3
  AND (NOT pp.requires_zone_choice OR pp.price_point_key = 'absheron:A')
ORDER BY price_rank;

-- 2. Who is competitive? Latest week, price index against the market median (100 = market).
SELECT pp.label, c.n_products, c.price_index_vs_market, c.share_cheapest_pct, c.promo_share_pct
FROM gold.mart_retailer_competitiveness c
JOIN gold.dim_price_point pp USING (price_point_key)
WHERE c.week_start = (SELECT max(week_start) FROM gold.mart_retailer_competitiveness)
ORDER BY c.price_index_vs_market;

-- 3. The most overstated promotions on the latest day: shelf tag vs market reality.
SELECT p.product_name, pp.label AS store,
       a.price_qepik, a.old_price_qepik AS shelf_was, a.market_ref_qepik AS market_price,
       a.claimed_discount_bp / 100.0 AS claimed_pct, a.real_discount_bp / 100.0 AS real_pct
FROM gold.mart_promo_analysis a
JOIN gold.dim_product p USING (product_key)
JOIN gold.dim_price_point pp USING (price_point_key)
WHERE a.observed_date = (SELECT max(observed_date) FROM gold.mart_promo_analysis)
  AND a.inflated_flag
ORDER BY a.claimed_discount_bp - a.real_discount_bp DESC
LIMIT 10;

-- 4. Inflation-like index by category: first and latest week.
SELECT category,
       max(index_regular) FILTER (WHERE week_start = (SELECT max(week_start) FROM gold.mart_price_index)) AS latest_index,
       max(wow_change_pct) FILTER (WHERE week_start = (SELECT max(week_start) FROM gold.mart_price_index)) AS last_week_change_pct
FROM gold.mart_price_index
GROUP BY category
ORDER BY latest_index DESC;

-- 5. Which chains raise prices most often? Share of price moves that are increases, by retailer.
SELECT retailer_code,
       sum(n_increases) AS increases, sum(n_decreases) AS decreases,
       round(100.0 * sum(n_increases) / nullif(sum(n_increases) + sum(n_decreases), 0), 1) AS pct_increases
FROM gold.mart_price_changes_daily
GROUP BY retailer_code
ORDER BY pct_increases DESC;

-- 6. Cheapest default basket per store on the latest day (complete baskets only).
SELECT pp.label, b.total_qepik / 100.0 AS basket_azn, b.rank_overall
FROM gold.mart_cheapest_basket b
JOIN gold.dim_price_point pp USING (price_point_key)
WHERE b.observed_date = (SELECT max(observed_date) FROM gold.mart_cheapest_basket) AND b.is_complete
ORDER BY b.total_qepik;

-- 7. How were items matched, and how sure are we?
SELECT method, status, count(*) AS items, round(avg(confidence), 3) AS avg_confidence
FROM silver.product_match
GROUP BY method, status
ORDER BY items DESC;

-- 8. Row lineage: nothing disappears between bronze and silver.
SELECT b.source,
       sum(b.row_count)                                                         AS bronze_rows,
       (SELECT count(*) FROM silver.price_observation o
          JOIN silver.store_item i ON i.id = o.store_item_id WHERE i.retailer_code = b.source) AS observations,
       (SELECT count(*) FROM silver.rejected_record r WHERE r.source = b.source) AS rejected
FROM bronze.ingest_batch b
WHERE b.kind = 'prices' AND b.status = 'silver_done'
GROUP BY b.source
ORDER BY b.source;

-- 9. Pipeline history: duration and outcome of recent runs.
SELECT run_id, status, as_of_date, round(extract(epoch FROM finished_at - started_at)) AS seconds
FROM ops.pipeline_run
ORDER BY started_at DESC
LIMIT 5;

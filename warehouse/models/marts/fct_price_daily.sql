{{ config(
    materialized='incremental',
    unique_key=['store_item_id', 'price_point_key', 'observed_date'],
    incremental_strategy='delete+insert',
    indexes=[{'columns': ['observed_date']}, {'columns': ['store_item_id', 'observed_date']}]
) }}
-- Grain: one row per store item, price point and day. Money columns are integer qepik.
-- Incremental: recent days are re-written (late corrections); history before the window is untouched.
-- The regular-price logic reads the full history in the staging view, so the window never starves it.
select
    to_char(observed_date, 'YYYYMMDD')::int as date_key,
    observed_date,
    store_item_id,
    retailer_code,
    price_point_key,
    price_qepik,
    old_price_qepik,
    regular_price_qepik,
    regular_price_source,
    is_promo,
    available,
    observed_at,
    n_stores,
    n_distinct_prices
from {{ ref('int_daily_price_regular') }}
{% if is_incremental() %}
where observed_date >= (select coalesce(max(observed_date), date '1900-01-01') - 7 from {{ this }})
{% endif %}

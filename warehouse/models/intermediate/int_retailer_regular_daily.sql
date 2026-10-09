{{ config(indexes=[{'columns': ['observed_date', 'product_key']}]) }}
-- Ordinary (non-promo) price per product, retailer and day: the market reference for promo checks.
select
    f.observed_date,
    b.product_key,
    f.retailer_code,
    (percentile_cont(0.5) within group (order by f.price_qepik))::int as regular_price_qepik
from {{ ref('fct_price_daily') }} f
join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
where not f.is_promo
group by 1, 2, 3

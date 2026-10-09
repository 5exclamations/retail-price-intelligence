-- Weekly average regular price per product and price point. Weeks with too few observed days are
-- dropped so a half-observed week cannot look like a price move.
select
    b.product_key,
    f.price_point_key,
    f.retailer_code,
    d.week_start,
    avg(f.regular_price_qepik)::numeric as regular_price_avg,
    avg(f.price_qepik)::numeric as effective_price_avg,
    avg(f.is_promo::int)::numeric as promo_share,
    count(distinct f.observed_date) as n_days
from {{ ref('fct_price_daily') }} f
join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
join {{ ref('dim_date') }} d on d.date_key = f.date_key
group by 1, 2, 3, 4
having count(distinct f.observed_date) >= {{ var('index_min_days_per_week') }}

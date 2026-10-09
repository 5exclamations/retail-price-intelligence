-- Day-over-day moves of the *regular* price (promotions do not count as price changes).
with moves as (
    select
        f.observed_date, f.retailer_code, b.product_key, p.category,
        f.regular_price_qepik,
        lag(f.regular_price_qepik) over (partition by f.store_item_id, f.price_point_key order by f.observed_date) as prev_qepik
    from {{ ref('fct_price_daily') }} f
    join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
    join {{ ref('dim_product') }} p on p.product_key = b.product_key
)
select
    observed_date, retailer_code, category,
    count(*) filter (where regular_price_qepik > prev_qepik) as n_increases,
    count(*) filter (where regular_price_qepik < prev_qepik) as n_decreases,
    count(*) as n_compared,
    (avg((regular_price_qepik - prev_qepik) * 10000.0 / prev_qepik) filter (where regular_price_qepik > prev_qepik))::int as avg_increase_bp,
    (avg((regular_price_qepik - prev_qepik) * 10000.0 / prev_qepik) filter (where regular_price_qepik < prev_qepik))::int as avg_decrease_bp
from moves
where prev_qepik is not null and prev_qepik > 0
group by 1, 2, 3

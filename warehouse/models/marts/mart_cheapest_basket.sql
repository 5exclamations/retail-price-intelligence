-- Daily cost of the default basket at every price point. total_qepik is an integer sum of
-- quantity x price; only price points that stock every basket item that day are "complete" and ranked.
with items as (select count(*) as n from {{ ref('basket_definition') }}),
priced as (
    select f.observed_date, f.price_point_key, b.product_key,
           min(f.price_qepik) as price_qepik
    from {{ ref('fct_price_daily') }} f
    join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
    join {{ ref('basket_definition') }} d on d.product_key = b.product_key
    where f.available
    group by 1, 2, 3
),
totals as (
    select pr.observed_date, pr.price_point_key,
           count(*) as items_available,
           sum(d.quantity * pr.price_qepik)::int as total_qepik
    from priced pr join {{ ref('basket_definition') }} d using (product_key)
    group by 1, 2
)
select
    t.observed_date, t.price_point_key, i.n as items_total, t.items_available, t.total_qepik,
    (t.items_available = i.n) as is_complete,
    case when t.items_available = i.n
         then rank() over (partition by t.observed_date, (t.items_available = i.n) order by t.total_qepik) end as rank_overall
from totals t, items i

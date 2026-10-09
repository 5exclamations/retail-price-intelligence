-- Default shopping basket: per category, the best-covered products that every retailer sells.
-- Built from data (not a hand-maintained list), so it stays valid when matching changes.
with retailers as (select count(*) as n from {{ ref('dim_retailer') }}),
candidates as (
    select p.product_key, p.product_name, p.category, p.retailer_count,
           row_number() over (partition by p.category order by p.retailer_count desc, p.store_item_count asc, p.product_key) as rk
    from {{ ref('dim_product') }} p, retailers r
    where p.retailer_count >= least({{ var('basket_min_retailers') }}, r.n)
)
select c.product_key, c.product_name, c.category, q.quantity
from candidates c
join {{ ref('basket_quantity') }} q on q.category = c.category
where c.rk <= {{ var('basket_items_per_category') }}

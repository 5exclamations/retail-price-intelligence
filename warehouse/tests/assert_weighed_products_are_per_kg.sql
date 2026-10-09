-- Weighed goods are compared kilogram to kilogram only: a weighed product may not share a canonical
-- product with a packaged one.
select p.product_key
from {{ ref('dim_product') }} p
join {{ source('silver', 'product_match') }} m on m.product_id = p.product_key
join {{ source('silver', 'store_item') }} i on i.id = m.store_item_id
group by p.product_key
having count(distinct (i.unit_type = 'kg_bulk')) > 1

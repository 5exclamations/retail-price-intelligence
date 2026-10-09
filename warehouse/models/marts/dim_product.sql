-- Published canonical products. Quarantined merges are excluded here and everywhere downstream.
select
    p.id as product_key,
    p.name as product_name,
    p.brand,
    p.category,
    p.unit_type,
    p.unit_value,
    p.pack,
    p.is_weighed,
    p.ean,
    count(distinct i.retailer_code) as retailer_count,
    count(i.id) as store_item_count
from {{ source('silver', 'product') }} p
join {{ source('silver', 'product_match') }} m on m.product_id = p.id
join {{ source('silver', 'store_item') }} i on i.id = m.store_item_id
where not p.quarantined
group by p.id, p.name, p.brand, p.category, p.unit_type, p.unit_value, p.pack, p.is_weighed, p.ean

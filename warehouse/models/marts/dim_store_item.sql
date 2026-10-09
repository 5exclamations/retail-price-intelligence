-- Bridge from a retailer's SKU to the canonical product. Facts stay keyed by SKU so that a changed
-- match (e.g. a human approves a review) reaches the whole history without rewriting the fact table.
select
    i.id as store_item_id,
    i.retailer_code,
    i.sku,
    i.name_raw,
    i.brand,
    i.ean,
    i.category,
    m.product_id as product_key,
    m.method as match_method,
    m.confidence as match_confidence,
    m.status as match_status
from {{ source('silver', 'store_item') }} i
join {{ source('silver', 'product_match') }} m on m.store_item_id = i.id
join {{ ref('dim_product') }} p on p.product_key = m.product_id

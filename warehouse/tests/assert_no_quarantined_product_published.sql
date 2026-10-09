-- Quarantined merges must never reach the gold layer.
select b.product_key
from {{ ref('dim_store_item') }} b
join {{ source('silver', 'product') }} p on p.id = b.product_key
where p.quarantined

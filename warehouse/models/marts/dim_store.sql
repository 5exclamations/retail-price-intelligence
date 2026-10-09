select
    s.id as store_key,
    s.retailer_code,
    s.store_code,
    s.name as store_name,
    s.format as store_format,
    s.price_zone,
    s.retailer_code || ':' || s.price_zone as price_point_key
from {{ source('silver', 'store') }} s

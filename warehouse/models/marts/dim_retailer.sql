select code as retailer_code, name as retailer_name, price_model
from {{ source('silver', 'retailer') }}

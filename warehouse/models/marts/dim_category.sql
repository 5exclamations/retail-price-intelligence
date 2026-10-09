select row_number() over (order by category)::int as category_key, category
from (select distinct category from {{ ref('dim_product') }}) c

{# A zoned retailer must be split by zone and a single-price retailer must not be.
   This encodes the rule "never show a zoned chain's price without choosing a zone". #}
{% test zoned_price_points_have_zone(model) %}
select pp.price_point_key
from {{ model }} pp
join {{ ref('dim_retailer') }} r on r.retailer_code = pp.retailer_code
where (r.price_model = 'zoned' and pp.price_zone = 'all')
   or (r.price_model = 'single' and pp.price_zone <> 'all')
{% endtest %}

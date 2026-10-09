{% snapshot snap_store_zone %}
{{ config(target_schema='gold', unique_key='id', strategy='check', check_cols=['price_zone']) }}
-- History of measured price zones per store: a store that moves zone keeps its old zone with dates.
select id, retailer_code, store_code, price_zone from {{ source('silver', 'store') }}
{% endsnapshot %}

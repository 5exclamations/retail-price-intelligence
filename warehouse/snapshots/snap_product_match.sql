{% snapshot snap_product_match %}
{{ config(target_schema='gold', unique_key='store_item_id', strategy='check', check_cols=['product_id', 'status', 'method']) }}
-- History of match decisions (SCD type 2): when a review or a re-run moves a SKU to another product,
-- the previous assignment is kept with its validity interval.
select store_item_id, product_id, status, method, confidence from {{ source('silver', 'product_match') }}
{% endsnapshot %}

{# Generic tests written locally so the project needs no package download (dbt deps). #}

{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

{% test positive(model, column_name) %}
select * from {{ model }} where {{ column_name }} is not null and {{ column_name }} <= 0
{% endtest %}

{# Money is integer qepik, never float: fails when the column type is anything but an integer type. #}
{% test integer_typed(model, column_name) %}
select 1
from information_schema.columns
where table_schema = '{{ model.schema }}'
  and table_name = '{{ model.identifier }}'
  and column_name = '{{ column_name }}'
  and data_type not in ('integer', 'bigint', 'smallint')
{% endtest %}

{% test between_values(model, column_name, min_value, max_value) %}
select * from {{ model }}
where {{ column_name }} is not null and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})
{% endtest %}

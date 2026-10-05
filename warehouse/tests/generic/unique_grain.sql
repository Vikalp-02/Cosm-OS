{#- Fails for every combination of `columns` that identifies more than one row. -#}
{% test unique_grain(model, columns) %}
select {{ columns | join(', ') }}
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

{#- Use the configured schema name as written, without dbt's target prefix. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name is not none else target.schema }}
{%- endmacro %}

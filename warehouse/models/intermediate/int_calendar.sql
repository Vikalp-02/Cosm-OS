{{ config(materialized='view') }}

-- The last day each tenant has any data for. Taken across sources so that a
-- source going quiet at the end still leaves those days on the calendar.
select tenant_id, max(date_day) as last_day
from (
    select tenant_id, date_day from {{ ref('stg_sales_daily') }}
    union all
    select tenant_id, date_day from {{ ref('int_shelf_city_daily') }}
)
group by tenant_id

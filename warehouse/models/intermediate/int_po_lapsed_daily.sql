-- Units that were due on a day and never arrived.
select
    tenant_id,
    platform,
    city,
    platform_item_id,
    expected_delivery_date as date_day,
    count(*) as po_lines_lapsed,
    sum(units_ordered) as po_units_lapsed
from {{ ref('stg_purchase_order_line') }}
where status = 'expired'
    and expected_delivery_date is not null
group by all

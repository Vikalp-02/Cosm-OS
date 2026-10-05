-- Warehouse stock per city. A city can be served by more than one facility.
select
    tenant_id,
    platform,
    city,
    platform_item_id,
    date_day,
    sum(units_on_hand) as units_on_hand,
    sum(open_po_units) as open_po_units
from {{ ref('stg_inventory_snapshot') }}
group by all

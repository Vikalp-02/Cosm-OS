select
    tenant_id,
    platform,
    city,
    facility_id,
    platform_item_id,
    snapshot_date as date_day,
    units_on_hand,
    open_po_units
from {{ source('raw', 'inventory_snapshot') }}

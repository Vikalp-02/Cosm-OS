select
    tenant_id,
    platform,
    city,
    facility_id,
    po_number,
    platform_item_id,
    order_date,
    expected_delivery_date,
    status,
    units_ordered,
    units_received,
    line_value
from {{ source('raw', 'purchase_order_line') }}

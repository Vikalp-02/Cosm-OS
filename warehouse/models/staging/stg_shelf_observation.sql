select
    tenant_id,
    platform,
    location_id,
    platform_item_id,
    observed_date as date_day,
    is_available,
    selling_price
from {{ source('raw', 'shelf_observation') }}

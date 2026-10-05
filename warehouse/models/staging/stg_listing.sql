select tenant_id, platform, platform_item_id, sku_id, ean
from {{ source('raw', 'listing') }}

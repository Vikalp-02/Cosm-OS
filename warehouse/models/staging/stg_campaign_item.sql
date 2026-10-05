select tenant_id, platform, campaign_id, platform_item_id
from {{ source('raw', 'campaign_item') }}

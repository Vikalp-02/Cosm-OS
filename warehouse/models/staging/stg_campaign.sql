select tenant_id, platform, campaign_id, campaign_name
from {{ source('raw', 'campaign') }}

select tenant_id, platform, campaign_id, report_date as date_day, daily_budget, is_active
from {{ source('raw', 'campaign_daily') }}

select
    tenant_id,
    platform,
    campaign_id,
    keyword,
    report_date as date_day,
    impressions,
    clicks,
    spend,
    attributed_orders,
    attributed_revenue
from {{ source('raw', 'ad_performance_daily') }}

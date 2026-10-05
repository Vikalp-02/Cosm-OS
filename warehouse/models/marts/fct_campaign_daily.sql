-- One row per campaign and day: its budget, and what it delivered.
with delivery as (
    select
        tenant_id,
        platform,
        campaign_id,
        date_day,
        sum(impressions) as impressions,
        sum(clicks) as clicks,
        sum(spend) as spend,
        sum(attributed_orders) as attributed_orders,
        sum(attributed_revenue) as attributed_revenue
    from {{ ref('stg_ad_performance_daily') }}
    group by all
),

advertised as (
    -- A campaign is filed under the category most of its listings belong to.
    select
        item.tenant_id,
        item.platform,
        item.campaign_id,
        mode(listing.category) as category,
        count(*) as listings_advertised
    from {{ ref('stg_campaign_item') }} as item
    inner join {{ ref('int_listing_product') }} as listing
        on item.tenant_id = listing.tenant_id
        and item.platform = listing.platform
        and item.platform_item_id = listing.platform_item_id
    group by all
)

select
    settings.tenant_id,
    settings.platform,
    settings.campaign_id,
    campaign.campaign_name,
    advertised.category,
    coalesce(advertised.listings_advertised, 0) as listings_advertised,
    settings.date_day,
    settings.is_active,
    settings.daily_budget,
    coalesce(delivery.impressions, 0) as impressions,
    coalesce(delivery.clicks, 0) as clicks,
    coalesce(delivery.spend, 0) as spend,
    coalesce(delivery.attributed_orders, 0) as attributed_orders,
    coalesce(delivery.attributed_revenue, 0) as attributed_revenue,
    coalesce(delivery.spend, 0) / nullif(settings.daily_budget, 0) as budget_utilisation
from {{ ref('stg_campaign_daily') }} as settings
inner join {{ ref('stg_campaign') }} as campaign
    on settings.tenant_id = campaign.tenant_id
    and settings.platform = campaign.platform
    and settings.campaign_id = campaign.campaign_id
left join advertised
    on settings.tenant_id = advertised.tenant_id
    and settings.platform = advertised.platform
    and settings.campaign_id = advertised.campaign_id
left join delivery
    on settings.tenant_id = delivery.tenant_id
    and settings.platform = delivery.platform
    and settings.campaign_id = delivery.campaign_id
    and settings.date_day = delivery.date_day

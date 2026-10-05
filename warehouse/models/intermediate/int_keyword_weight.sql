-- How much each search term matters to a tenant on a platform.
--
-- Platforms do not share search volumes. The orders a term brings in through
-- ads are the closest available stand-in for how much demand flows through it,
-- so each term is weighted by its share of those orders. A term that was never
-- advertised gets the average weight.
with ordered as (
    select
        tenant_id,
        platform,
        keyword,
        sum(attributed_orders) as attributed_orders
    from {{ ref('stg_ad_performance_daily') }}
    group by all
),

searched as (
    select distinct tenant_id, platform, keyword
    from {{ ref('stg_search_rank_observation') }}
),

typical as (
    select tenant_id, platform, avg(attributed_orders) as attributed_orders
    from ordered
    where attributed_orders > 0
    group by all
)

select
    searched.tenant_id,
    searched.platform,
    searched.keyword,
    coalesce(nullif(ordered.attributed_orders, 0), typical.attributed_orders, 1) as weight
from searched
left join ordered
    on searched.tenant_id = ordered.tenant_id
    and searched.platform = ordered.platform
    and searched.keyword = ordered.keyword
left join typical
    on searched.tenant_id = typical.tenant_id
    and searched.platform = typical.platform

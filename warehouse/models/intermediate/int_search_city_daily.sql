-- Search results rolled up to the city.
--
-- The crawl records where a result sat on the page, sponsored slots included.
-- Organic rank is the position among organic results only, so it does not move
-- just because an ad appeared above or dropped away.
with organic as (
    select
        tenant_id,
        platform,
        pincode,
        keyword,
        platform_item_id,
        date_day,
        row_number() over (
            partition by tenant_id, platform, date_day, pincode, keyword
            order by search_rank
        ) as organic_rank
    from {{ ref('stg_search_rank_observation') }}
    where not is_sponsored
),

organic_by_city as (
    -- One organic result is one search the listing appeared in. Searches count
    -- towards visibility in proportion to how much the search term matters.
    select
        organic.tenant_id,
        organic.platform,
        place.city,
        organic.platform_item_id,
        organic.date_day,
        count(*) as organic_results,
        avg(organic.organic_rank) as avg_organic_rank,
        sum(term.weight / organic.organic_rank) / sum(term.weight) as organic_reciprocal_rank
    from organic
    inner join {{ ref('int_pincode_city') }} as place
        on organic.platform = place.platform
        and organic.pincode = place.pincode
    inner join {{ ref('int_keyword_weight') }} as term
        on organic.tenant_id = term.tenant_id
        and organic.platform = term.platform
        and organic.keyword = term.keyword
    group by all
),

sponsored_by_city as (
    select
        search.tenant_id,
        search.platform,
        place.city,
        search.platform_item_id,
        search.date_day,
        count(*) as sponsored_placements
    from {{ ref('stg_search_rank_observation') }} as search
    inner join {{ ref('int_pincode_city') }} as place
        on search.platform = place.platform
        and search.pincode = place.pincode
    where search.is_sponsored
    group by all
)

select
    organic_by_city.tenant_id,
    organic_by_city.platform,
    organic_by_city.city,
    organic_by_city.platform_item_id,
    organic_by_city.date_day,
    organic_by_city.organic_results,
    organic_by_city.avg_organic_rank,
    organic_by_city.organic_reciprocal_rank,
    coalesce(sponsored_by_city.sponsored_placements, 0) as sponsored_placements
from organic_by_city
left join sponsored_by_city
    on organic_by_city.tenant_id = sponsored_by_city.tenant_id
    and organic_by_city.platform = sponsored_by_city.platform
    and organic_by_city.city = sponsored_by_city.city
    and organic_by_city.platform_item_id = sponsored_by_city.platform_item_id
    and organic_by_city.date_day = sponsored_by_city.date_day

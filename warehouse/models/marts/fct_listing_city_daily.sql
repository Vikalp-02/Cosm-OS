-- One row per product, city and day, from the product's first sale in that
-- city onward: what sold, and every driver that could explain why.
--
-- Sales are null, not zero, on days the city's sales never arrived.
with first_sales as (
    select tenant_id, platform, city, platform_item_id, min(date_day) as first_day
    from {{ ref('stg_sales_daily') }}
    group by all
),

spine as (
    select
        first_sales.tenant_id,
        first_sales.platform,
        first_sales.city,
        first_sales.platform_item_id,
        unnest(generate_series(first_sales.first_day, calendar.last_day, interval 1 day))::date
            as date_day
    from first_sales
    inner join {{ ref('int_calendar') }} as calendar
        on first_sales.tenant_id = calendar.tenant_id
),

joined as (
    select
        spine.tenant_id,
        spine.platform,
        spine.city,
        listing.sku_id,
        spine.platform_item_id,
        listing.brand,
        listing.category,
        spine.date_day,

        reporting.is_reported as sales_reported,
        case when reporting.is_reported then coalesce(sales.units_sold, 0) end as units_sold,
        case when reporting.is_reported then coalesce(sales.gmv, 0) end as gmv,

        shelf.stores_checked,
        shelf.availability,
        shelf.avg_selling_price,
        shelf.avg_selling_price / listing.mrp as price_to_mrp,
        rivals.rival_price_to_mrp,

        search.organic_results,
        search.avg_organic_rank,
        search.organic_reciprocal_rank,
        search.sponsored_placements / nullif(search.organic_results, 0) as sponsored_share,

        stock.units_on_hand,
        stock.open_po_units,
        coalesce(lapsed.po_units_lapsed, 0) as po_units_lapsed
    from spine
    inner join {{ ref('int_listing_product') }} as listing
        on spine.tenant_id = listing.tenant_id
        and spine.platform = listing.platform
        and spine.platform_item_id = listing.platform_item_id
    inner join {{ ref('fct_sales_reporting_daily') }} as reporting
        on spine.tenant_id = reporting.tenant_id
        and spine.platform = reporting.platform
        and spine.city = reporting.city
        and spine.date_day = reporting.date_day
    left join {{ ref('stg_sales_daily') }} as sales
        on spine.tenant_id = sales.tenant_id
        and spine.platform = sales.platform
        and spine.city = sales.city
        and spine.platform_item_id = sales.platform_item_id
        and spine.date_day = sales.date_day
    left join {{ ref('int_shelf_city_daily') }} as shelf
        on spine.tenant_id = shelf.tenant_id
        and spine.platform = shelf.platform
        and spine.city = shelf.city
        and spine.platform_item_id = shelf.platform_item_id
        and spine.date_day = shelf.date_day
    left join {{ ref('int_rival_price_city_daily') }} as rivals
        on spine.tenant_id = rivals.tenant_id
        and spine.platform = rivals.platform
        and spine.city = rivals.city
        and listing.category = rivals.category
        and spine.date_day = rivals.date_day
    left join {{ ref('int_search_city_daily') }} as search
        on spine.tenant_id = search.tenant_id
        and spine.platform = search.platform
        and spine.city = search.city
        and spine.platform_item_id = search.platform_item_id
        and spine.date_day = search.date_day
    left join {{ ref('int_inventory_city_daily') }} as stock
        on spine.tenant_id = stock.tenant_id
        and spine.platform = stock.platform
        and spine.city = stock.city
        and spine.platform_item_id = stock.platform_item_id
        and spine.date_day = stock.date_day
    left join {{ ref('int_po_lapsed_daily') }} as lapsed
        on spine.tenant_id = lapsed.tenant_id
        and spine.platform = lapsed.platform
        and spine.city = lapsed.city
        and spine.platform_item_id = lapsed.platform_item_id
        and spine.date_day = lapsed.date_day
)

select
    *,
    -- Unreported days are null and so drop out of the average.
    units_on_hand / nullif(avg(units_sold) over recent_days, 0) as days_of_cover
from joined
window recent_days as (
    partition by tenant_id, platform, city, platform_item_id
    order by date_day
    rows between {{ var('cover_window_days') }} preceding and 1 preceding
)

-- How deeply competitors in a category are discounting, per city and day.
-- Price over MRP is averaged, not price, so a rival missing from one day's
-- crawl does not move the figure.
select
    shelf.tenant_id,
    shelf.platform,
    shelf.city,
    listing.category,
    shelf.date_day,
    count(*) as rivals_priced,
    avg(shelf.avg_selling_price / listing.mrp) as rival_price_to_mrp
from {{ ref('int_shelf_city_daily') }} as shelf
inner join {{ ref('int_listing_product') }} as listing
    on shelf.tenant_id = listing.tenant_id
    and shelf.platform = listing.platform
    and shelf.platform_item_id = listing.platform_item_id
where listing.is_competitor
    and shelf.avg_selling_price is not null
group by all

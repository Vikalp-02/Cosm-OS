-- Dark-store shelf checks rolled up to the city, the grain sales arrive at.
select
    shelf.tenant_id,
    shelf.platform,
    store.city,
    shelf.platform_item_id,
    shelf.date_day,
    count(*) as stores_checked,
    avg(shelf.is_available::int) as availability,
    avg(shelf.selling_price) as avg_selling_price
from {{ ref('stg_shelf_observation') }} as shelf
inner join {{ ref('stg_location') }} as store
    on shelf.platform = store.platform
    and shelf.location_id = store.location_id
group by all

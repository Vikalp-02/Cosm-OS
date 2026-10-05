-- A crawled pincode with no known city would silently drop out of the search
-- roll-up, and visibility would be measured on fewer searches than were run.
select distinct search.platform, search.pincode
from {{ ref('stg_search_rank_observation') }} as search
left join {{ ref('int_pincode_city') }} as place
    on search.platform = place.platform
    and search.pincode = place.pincode
where place.city is null

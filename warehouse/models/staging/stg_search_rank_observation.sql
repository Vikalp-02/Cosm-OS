select
    tenant_id,
    platform,
    pincode,
    keyword,
    platform_item_id,
    observed_date as date_day,
    is_sponsored,
    search_rank
from {{ source('raw', 'search_rank_observation') }}

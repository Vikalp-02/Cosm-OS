{{ config(materialized='view') }}

-- Search rank is crawled per pincode with no city attached. A platform's own
-- store list is what places a pincode in a city.
select distinct platform, pincode, city
from {{ ref('stg_location') }}

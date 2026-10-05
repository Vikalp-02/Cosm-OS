select platform, location_id, pincode, city, state
from {{ ref('stg_location') }}

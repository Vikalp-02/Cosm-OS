select platform, location_id, pincode, city, state
from {{ source('raw', 'location') }}

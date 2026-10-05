select tenant_id, platform, city, platform_item_id, report_date as date_day, units_sold, gmv
from {{ source('raw', 'sales_daily') }}

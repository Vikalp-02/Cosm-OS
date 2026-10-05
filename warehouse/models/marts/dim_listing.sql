select
    tenant_id,
    platform,
    platform_item_id,
    sku_id,
    brand,
    product_name,
    category,
    is_competitor,
    mrp
from {{ ref('int_listing_product') }}

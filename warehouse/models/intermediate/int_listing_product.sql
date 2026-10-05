{{ config(materialized='view') }}

-- Every listing with the product it sells. Sources report by platform item id;
-- everything downstream reasons about products.
select
    listing.tenant_id,
    listing.platform,
    listing.platform_item_id,
    product.sku_id,
    product.brand,
    product.product_name,
    product.category,
    product.is_competitor,
    product.mrp
from {{ ref('stg_listing') }} as listing
inner join {{ ref('stg_product') }} as product
    on listing.tenant_id = product.tenant_id
    and listing.sku_id = product.sku_id

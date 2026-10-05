select tenant_id, sku_id, brand, product_name, category, is_competitor, mrp
from {{ ref('stg_product') }}

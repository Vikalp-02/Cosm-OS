-- Every unit and rupee in the source appears exactly once in the mart. A join
-- that drops or repeats rows shows up here.
with source as (
    select sum(units_sold) as units_sold, sum(gmv) as gmv
    from {{ ref('stg_sales_daily') }}
),

mart as (
    select sum(units_sold) as units_sold, sum(gmv) as gmv
    from {{ ref('fct_listing_city_daily') }}
)

select
    source.units_sold as source_units_sold,
    mart.units_sold as mart_units_sold,
    source.gmv as source_gmv,
    mart.gmv as mart_gmv
from source
cross join mart
where source.units_sold is distinct from mart.units_sold
    or source.gmv is distinct from mart.gmv

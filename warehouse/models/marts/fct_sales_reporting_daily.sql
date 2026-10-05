-- Whether each city's sales arrived on each day.
--
-- Platforms send a row only for what sold, so a missing row can mean nothing
-- sold or nothing arrived. A city that normally reports and then sends nothing
-- at all is treated as a gap in the data, not as a day without sales.
with reported as (
    select
        tenant_id,
        platform,
        city,
        date_day,
        count(*) as rows_reported,
        sum(units_sold) as units_reported
    from {{ ref('stg_sales_daily') }}
    group by all
),

cities as (
    select tenant_id, platform, city, min(date_day) as first_day
    from reported
    group by all
),

days as (
    select
        cities.tenant_id,
        cities.platform,
        cities.city,
        unnest(generate_series(cities.first_day, calendar.last_day, interval 1 day))::date
            as date_day
    from cities
    inner join {{ ref('int_calendar') }} as calendar
        on cities.tenant_id = calendar.tenant_id
),

joined as (
    select
        days.tenant_id,
        days.platform,
        days.city,
        days.date_day,
        reported.rows_reported is not null as is_reported,
        coalesce(reported.rows_reported, 0) as rows_reported,
        reported.units_reported
    from days
    left join reported
        on days.tenant_id = reported.tenant_id
        and days.platform = reported.platform
        and days.city = reported.city
        and days.date_day = reported.date_day
),

history as (
    select
        *,
        count(*) over prior_days as days_of_history,
        avg(is_reported::int) over prior_days as prior_reporting_rate
    from joined
    window prior_days as (
        partition by tenant_id, platform, city
        order by date_day
        rows between {{ var('reporting_window_days') }} preceding and 1 preceding
    )
)

select
    tenant_id,
    platform,
    city,
    date_day,
    is_reported,
    rows_reported,
    units_reported,
    days_of_history,
    prior_reporting_rate,
    (
        not is_reported
        and days_of_history >= {{ var('reporting_gap_min_history_days') }}
        and prior_reporting_rate >= {{ var('reporting_gap_min_rate') }}
    ) as is_gap
from history

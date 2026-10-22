-- PropTech 360 analytical queries.
-- Reporting period: January–June 2026.


-- =========================================================
-- 1. Rank properties by average monthly occupancy.
-- =========================================================
-- Average the six monthly rates, excluding NULL rates.
-- Unit-month totals provide numerator and denominator context.

WITH occupancy_summary AS (
    SELECT
        p.property_id,
        p.property_name,
        p.city,
        COUNT(*) AS reporting_months,
        COUNT(f.occupancy_rate) AS months_with_defined_occupancy,
        SUM(f.occupied_units) AS occupied_unit_months,
        SUM(f.total_units) AS available_unit_months,
        AVG(f.occupancy_rate) AS average_monthly_occupancy
    FROM public.fact_property_month AS f
    JOIN public.dim_property AS p
        ON p.property_id = f.property_id
    WHERE f.month BETWEEN DATE '2026-01-01' AND DATE '2026-06-01'
    GROUP BY p.property_id, p.property_name, p.city
)
SELECT
    DENSE_RANK() OVER (
        ORDER BY average_monthly_occupancy DESC NULLS LAST
    ) AS occupancy_rank,
    property_id,
    property_name,
    city,
    reporting_months,
    months_with_defined_occupancy,
    occupied_unit_months,
    available_unit_months,
    ROUND(average_monthly_occupancy * 100, 2)
        AS average_monthly_occupancy_pct
FROM occupancy_summary
ORDER BY occupancy_rank, property_id;


-- =========================================================
-- 2. Monthly earned contractual rent by city.
-- =========================================================
-- This is month-end contractual rent, not payment receipts.

SELECT
    f.month,
    p.city,
    COUNT(*) AS property_count,
    SUM(f.earned_monthly_rent_usd) AS earned_monthly_rent_usd
FROM public.fact_property_month AS f
JOIN public.dim_property AS p
    ON p.property_id = f.property_id
WHERE f.month BETWEEN DATE '2026-01-01' AND DATE '2026-06-01'
GROUP BY f.month, p.city
ORDER BY f.month, p.city;


-- =========================================================
-- 3. Maintenance SLA compliance by property.
-- =========================================================
-- Divide combined compliant counts by combined eligible counts.
-- Do not average monthly percentages.
-- Orders are assigned to opened month using source-extract status.

SELECT
    p.property_id,
    p.property_name,
    SUM(f.total_orders) AS total_orders,
    SUM(f.resolved_orders) AS resolved_orders,
    SUM(f.sla_eligible_orders) AS eligible_resolved_orders,
    SUM(f.sla_compliant_orders) AS compliant_orders,
    SUM(f.sla_noncompliant_orders) AS noncompliant_orders,
    SUM(f.open_orders) AS excluded_open_orders,
    SUM(f.missing_response_resolved_orders)
        AS excluded_missing_response_orders,
    ROUND(
        100.0 * SUM(f.sla_compliant_orders)
        / NULLIF(SUM(f.sla_eligible_orders), 0),
        2
    ) AS sla_compliance_pct
FROM public.fact_property_month AS f
JOIN public.dim_property AS p
    ON p.property_id = f.property_id
WHERE f.month BETWEEN DATE '2026-01-01' AND DATE '2026-06-01'
GROUP BY p.property_id, p.property_name
ORDER BY sla_compliance_pct DESC NULLS LAST, p.property_id;


-- =========================================================
-- 4. Energy intensity above the portfolio monthly average.
-- =========================================================
-- Define portfolio intensity as total known kWh divided by
-- corresponding active floor area: an area-weighted benchmark.
-- Include coverage so incomplete readings remain visible.

WITH monthly_benchmark AS (
    SELECT
        month,
        SUM(energy_kwh) FILTER (
            WHERE floor_area_sqm > 0
        )
        / NULLIF(
            SUM(floor_area_sqm) FILTER (
                WHERE energy_kwh IS NOT NULL
                  AND floor_area_sqm > 0
            ),
            0
        ) AS portfolio_energy_intensity_kwh_sqm
    FROM public.fact_property_month
    WHERE month BETWEEN DATE '2026-01-01' AND DATE '2026-06-01'
    GROUP BY month
)
SELECT
    f.property_id,
    p.property_name,
    f.month,
    f.energy_kwh,
    f.floor_area_sqm,
    ROUND(f.energy_intensity_kwh_sqm, 4)
        AS energy_intensity_kwh_sqm,
    ROUND(b.portfolio_energy_intensity_kwh_sqm, 4)
        AS portfolio_energy_intensity_kwh_sqm,
    ROUND(f.energy_reading_coverage_rate * 100, 2)
        AS energy_reading_coverage_pct
FROM public.fact_property_month AS f
JOIN public.dim_property AS p
    ON p.property_id = f.property_id
JOIN monthly_benchmark AS b
    ON b.month = f.month
WHERE f.energy_intensity_kwh_sqm
    > b.portfolio_energy_intensity_kwh_sqm
ORDER BY f.month, f.energy_intensity_kwh_sqm DESC;


-- =========================================================
-- 5. Portfolio dashboard view: one row per month.
-- =========================================================
-- Calculate portfolio rates from summed numerators/denominators.
-- Keep undefined rates NULL instead of presenting them as 0%.

CREATE OR REPLACE VIEW public.vw_portfolio_dashboard AS
SELECT
    month,
    COUNT(*) AS property_count,

    SUM(total_units) AS total_units,
    SUM(occupied_units) AS occupied_units,

    SUM(occupied_units)::NUMERIC
        / NULLIF(SUM(total_units), 0)
        AS occupancy_rate,

    SUM(earned_monthly_rent_usd) AS earned_monthly_rent_usd,

    SUM(resolved_orders) AS resolved_orders,
    SUM(sla_eligible_orders) AS sla_eligible_orders,
    SUM(sla_compliant_orders) AS sla_compliant_orders,
    SUM(sla_noncompliant_orders) AS sla_noncompliant_orders,
    SUM(open_orders) AS open_orders,
    SUM(missing_response_resolved_orders)
        AS missing_response_resolved_orders,

    SUM(sla_compliant_orders)::NUMERIC
        / NULLIF(SUM(sla_eligible_orders), 0)
        AS sla_compliance_rate,

    SUM(energy_kwh) AS energy_kwh,
    SUM(floor_area_sqm) AS floor_area_sqm,

    -- Include area only where a calculable energy total exists.
    SUM(energy_kwh) FILTER (
        WHERE floor_area_sqm > 0
    )
        / NULLIF(
            SUM(floor_area_sqm) FILTER (
                WHERE energy_kwh IS NOT NULL
                  AND floor_area_sqm > 0
            ),
            0
        ) AS energy_intensity_kwh_sqm,

    SUM(units_with_energy_reading) AS units_with_energy_reading,

    SUM(units_with_energy_reading)::NUMERIC
        / NULLIF(SUM(total_units), 0)
        AS energy_reading_coverage_rate

FROM public.fact_property_month
GROUP BY month;


-- Display the dashboard for the assessed reporting period.
SELECT *
FROM public.vw_portfolio_dashboard
WHERE month BETWEEN DATE '2026-01-01' AND DATE '2026-06-01'
ORDER BY month;
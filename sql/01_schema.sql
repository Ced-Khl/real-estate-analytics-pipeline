-- PropTech 360 dimensional mart.
-- Run this script while connected to the proptech360 database.

BEGIN;

-- One row per property.
-- Use the source property_id as the natural primary key.
CREATE TABLE IF NOT EXISTS dim_property (
    property_id   TEXT PRIMARY KEY,
    property_name TEXT NOT NULL,
    city          TEXT NOT NULL,

    CHECK (btrim(property_id) <> ''),
    CHECK (btrim(property_name) <> ''),
    CHECK (btrim(city) <> '')
);


-- One row per reporting month.
-- The first day of the month serves as the natural primary key.
CREATE TABLE IF NOT EXISTS dim_month (
    month DATE PRIMARY KEY,

    year_number INTEGER GENERATED ALWAYS AS (
        EXTRACT(YEAR FROM month)::INTEGER
    ) STORED,

    month_number INTEGER GENERATED ALWAYS AS (
        EXTRACT(MONTH FROM month)::INTEGER
    ) STORED,

    CHECK (EXTRACT(DAY FROM month) = 1)
);


-- Grain: one property per reporting month.
-- The composite primary key also supports idempotent upserts.
CREATE TABLE IF NOT EXISTS fact_property_month (
    property_id TEXT NOT NULL
        REFERENCES dim_property(property_id),

    month DATE NOT NULL
        REFERENCES dim_month(month),

    total_units    INTEGER NOT NULL CHECK (total_units >= 0),
    occupied_units INTEGER NOT NULL CHECK (occupied_units >= 0),

    earned_monthly_rent_usd NUMERIC(18, 2) NOT NULL
        CHECK (earned_monthly_rent_usd >= 0),

    -- Maintenance is grouped by opened month using source-extract status.
    total_orders       INTEGER NOT NULL CHECK (total_orders >= 0),
    resolved_orders    INTEGER NOT NULL CHECK (resolved_orders >= 0),
    sla_eligible_orders INTEGER NOT NULL CHECK (sla_eligible_orders >= 0),
    sla_compliant_orders INTEGER NOT NULL
        CHECK (sla_compliant_orders >= 0),
    sla_noncompliant_orders INTEGER NOT NULL
        CHECK (sla_noncompliant_orders >= 0),
    open_orders INTEGER NOT NULL CHECK (open_orders >= 0),
    missing_response_resolved_orders INTEGER NOT NULL
        CHECK (missing_response_resolved_orders >= 0),

    -- NULL consumption means no known total; it is not assumed to be zero.
    energy_kwh NUMERIC(18, 2) CHECK (energy_kwh >= 0),

    -- Area includes all active units, whether occupied or vacant.
    floor_area_sqm NUMERIC(18, 2) NOT NULL
        CHECK (floor_area_sqm >= 0),

    units_with_energy_reading INTEGER NOT NULL
        CHECK (units_with_energy_reading >= 0),

    -- NULLIF prevents division by zero and produces NULL rates
    -- when a denominator is zero.
    occupancy_rate NUMERIC GENERATED ALWAYS AS (
        occupied_units::NUMERIC / NULLIF(total_units, 0)
    ) STORED,

    sla_compliance_rate NUMERIC GENERATED ALWAYS AS (
        sla_compliant_orders::NUMERIC
        / NULLIF(sla_eligible_orders, 0)
    ) STORED,

    energy_intensity_kwh_sqm NUMERIC GENERATED ALWAYS AS (
        energy_kwh / NULLIF(floor_area_sqm, 0)
    ) STORED,

    energy_reading_coverage_rate NUMERIC GENERATED ALWAYS AS (
        units_with_energy_reading::NUMERIC
        / NULLIF(total_units, 0)
    ) STORED,

    PRIMARY KEY (property_id, month),

    -- Ensure counts and denominators remain consistent.
    CHECK (occupied_units <= total_units),
    CHECK (units_with_energy_reading <= total_units),

    CHECK (
        total_orders = resolved_orders + open_orders
    ),

    CHECK (
        resolved_orders =
        sla_eligible_orders + missing_response_resolved_orders
    ),

    CHECK (
        sla_eligible_orders =
        sla_compliant_orders + sla_noncompliant_orders
    ),

    CHECK (
        occupancy_rate IS NULL
        OR occupancy_rate BETWEEN 0 AND 1
    ),

    CHECK (
        sla_compliance_rate IS NULL
        OR sla_compliance_rate BETWEEN 0 AND 1
    )
);


-- The primary key already indexes (property_id, month).
-- Add a month-first index for portfolio-wide monthly reporting.
CREATE INDEX IF NOT EXISTS idx_fact_property_month_month
    ON fact_property_month(month);

-- Support filtering and grouping properties by city.
CREATE INDEX IF NOT EXISTS idx_dim_property_city
    ON dim_property(city);

COMMIT;
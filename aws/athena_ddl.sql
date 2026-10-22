# Documentation of how I created the Athena table




CREATE DATABASE IF NOT EXISTS proptech360;

from pathlib import Path
import pyarrow.parquet as pq

# Inspect an actual saved Parquet file.
# Month is stored in folder names, so it won't appear inside this file.
parquet_files = sorted(
    Path("data/curated/property_month_kpis").glob("month=*/*.parquet")
)

if not parquet_files:
    raise FileNotFoundError("No curated Parquet files found.")

print(pq.read_schema(parquet_files[0]))



CREATE EXTERNAL TABLE IF NOT EXISTS proptech360.property_month_kpis (
    property_id STRING,
    total_units BIGINT,
    occupied_units BIGINT,
    earned_monthly_rent_usd DECIMAL(7, 2),
    floor_area_sqm DOUBLE,
    occupancy_rate DOUBLE,
    total_orders BIGINT,
    resolved_orders BIGINT,
    sla_eligible_orders BIGINT,
    sla_compliant_orders BIGINT,
    sla_noncompliant_orders BIGINT,
    open_orders BIGINT,
    missing_response_resolved_orders BIGINT,
    sla_compliance_rate DOUBLE,
    energy_kwh DECIMAL(7, 2),
    units_with_energy_reading BIGINT,
    energy_reading_coverage_rate DOUBLE,
    energy_intensity_kwh_sqm DOUBLE
)
PARTITIONED BY (
    month STRING
)
STORED AS PARQUET
LOCATION 's3://proptech360/curated/property_month_kpis/'
TBLPROPERTIES (
    'classification' = 'parquet'
);



MSCK REPAIR TABLE proptech360.property_month_kpis;


SELECT
    month,
    COUNT(*) AS row_count
FROM proptech360.property_month_kpis
GROUP BY month
ORDER BY month;


SELECT *
FROM proptech360.property_month_kpis
ORDER BY property_id, month
LIMIT 5;


SELECT
    property_id,
    month,
    occupied_units,
    total_units,
    ROUND(occupancy_rate * 100, 2) AS occupancy_pct
FROM proptech360.property_month_kpis
ORDER BY occupancy_rate DESC, property_id, month
LIMIT 5;


WITH expected (month, expected_energy_kwh) AS (
    VALUES
        ('2026-01-01', DECIMAL '160098.66'),
        ('2026-02-01', DECIMAL '169242.08'),
        ('2026-03-01', DECIMAL '166593.93'),
        ('2026-04-01', DECIMAL '165089.40'),
        ('2026-05-01', DECIMAL '167188.95'),
        ('2026-06-01', DECIMAL '161039.67')
),
actual AS (
    SELECT
        month,
        COUNT(*) AS property_count,
        SUM(energy_kwh) AS actual_energy_kwh
    FROM proptech360.property_month_kpis
    GROUP BY month
)
SELECT
    COALESCE(a.month, e.month) AS month,
    a.property_count,
    a.actual_energy_kwh,
    e.expected_energy_kwh,
    a.actual_energy_kwh - e.expected_energy_kwh AS difference_kwh,
    CASE
        WHEN a.property_count = 12
         AND a.actual_energy_kwh = e.expected_energy_kwh
        THEN 'PASS'
        ELSE 'FAIL'
    END AS reconciliation_status
FROM actual AS a
FULL OUTER JOIN expected AS e
    ON a.month = e.month
ORDER BY COALESCE(a.month, e.month);


MSCK REPAIR TABLE proptech360.property_month_kpis;
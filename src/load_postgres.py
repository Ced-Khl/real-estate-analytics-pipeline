"""Load PropTech 360 dimensions and curated KPIs into PostgreSQL."""

from pathlib import Path
from datetime import date
from decimal import Decimal
import os

import pandas as pd
import pyarrow.parquet as pq
import psycopg


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load stored inputs rather than relying on notebook variables.
PROPERTY_PATH = PROJECT_ROOT / "data" / "raw" / "properties.csv"
PARQUET_PATH = PROJECT_ROOT / "data" / "curated" / "property_month_kpis"

# Exclude generated rate columns: PostgreSQL calculates them.
FACT_COLUMNS = [
    "property_id",
    "month",
    "total_units",
    "occupied_units",
    "earned_monthly_rent_usd",
    "total_orders",
    "resolved_orders",
    "sla_eligible_orders",
    "sla_compliant_orders",
    "sla_noncompliant_orders",
    "open_orders",
    "missing_response_resolved_orders",
    "energy_kwh",
    "floor_area_sqm",
    "units_with_energy_reading",
]

INTEGER_COLUMNS = {
    "total_units",
    "occupied_units",
    "total_orders",
    "resolved_orders",
    "sla_eligible_orders",
    "sla_compliant_orders",
    "sla_noncompliant_orders",
    "open_orders",
    "missing_response_resolved_orders",
    "units_with_energy_reading",
}

DECIMAL_COLUMNS = {
    "earned_monthly_rent_usd",
    "energy_kwh",
    "floor_area_sqm",
}


def read_load_inputs():
    """Read and validate the saved source dimension and curated facts."""
    properties = pd.read_csv(
        PROPERTY_PATH,
        dtype="string",
        keep_default_na=False,
    )

    required_properties = ["property_id", "property_name", "city"]

    if not set(required_properties).issubset(properties.columns):
        raise ValueError("Properties file is missing required columns.")

    properties = properties[required_properties].copy()

    # Reject invalid dimension inputs rather than silently deduplicate them.
    if properties.empty:
        raise ValueError("Properties file contains no records.")

    if properties["property_id"].duplicated().any():
        raise ValueError("Properties file contains duplicate identifiers.")

    for column in required_properties:
        if properties[column].str.strip().eq("").any():
            raise ValueError(f"Properties contains blank {column} values.")

    facts = pq.read_table(str(PARQUET_PATH)).to_pandas()

    missing_columns = set(FACT_COLUMNS) - set(facts.columns)
    if missing_columns:
        raise ValueError(
            f"Parquet is missing columns: {sorted(missing_columns)}"
        )

    facts = facts[FACT_COLUMNS].copy()
    facts["month"] = pd.to_datetime(
        facts["month"].astype("string"),
        format="%Y-%m-%d",
        errors="raise",
    ).dt.date

    expected_months = {
        date(2026, month, 1)
        for month in range(1, 7)
    }

    if set(facts["month"]) != expected_months:
        raise ValueError("Expected January–June 2026 in curated data.")

    if len(properties) != 12 or len(facts) != 72:
        raise ValueError("Expected 12 properties and 72 KPI rows.")

    if facts.duplicated(["property_id", "month"]).any():
        raise ValueError("Duplicate property-month keys in Parquet.")

    expected_keys = {
        (property_id, month)
        for property_id in properties["property_id"]
        for month in expected_months
    }
    actual_keys = set(
        facts[["property_id", "month"]].itertuples(
            index=False, name=None
        )
    )

    if actual_keys != expected_keys:
        raise ValueError("Curated property-month coverage is incomplete.")

    return properties, facts


def convert_value(column, value):
    """Convert pandas values into types accepted by psycopg."""
    if pd.isna(value):
        return None

    if column in INTEGER_COLUMNS:
        # Reject fractional counts instead of truncating them.
        number = Decimal(str(value))
        if number != number.to_integral_value():
            raise ValueError(f"Non-integer count in {column}: {value}")
        return int(number)

    if column in DECIMAL_COLUMNS:
        return Decimal(str(value))

    if column == "property_id":
        return str(value)

    return value


def load_postgres(connection_parameters):
    """Upsert dimensions and facts, then reconcile stored values."""
    properties, facts = read_load_inputs()

    property_rows = list(
        properties.itertuples(index=False, name=None)
    )
    month_rows = [(month,) for month in sorted(set(facts["month"]))]

    fact_rows = [
        tuple(
            convert_value(column, value)
            for column, value in zip(FACT_COLUMNS, row)
        )
        for row in facts.itertuples(index=False, name=None)
    ]

    # SQL identifiers below come only from the fixed column list,
    # while every record value is passed as a parameter.
    column_names = ", ".join(FACT_COLUMNS)
    placeholders = ", ".join(["%s"] * len(FACT_COLUMNS))
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in FACT_COLUMNS
        if column not in {"property_id", "month"}
    )

    fact_upsert = f"""
        INSERT INTO public.fact_property_month ({column_names})
        VALUES ({placeholders})
        ON CONFLICT (property_id, month)
        DO UPDATE SET {updates};
    """

    # Commit only if loading and verification both succeed.
    # Any exception rolls back the entire transaction.
    with psycopg.connect(**connection_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO public.dim_property
                    (property_id, property_name, city)
                VALUES (%s, %s, %s)
                ON CONFLICT (property_id)
                DO UPDATE SET
                    property_name = EXCLUDED.property_name,
                    city = EXCLUDED.city;
                """,
                property_rows,
            )

            cursor.executemany(
                """
                INSERT INTO public.dim_month (month)
                VALUES (%s)
                ON CONFLICT (month) DO NOTHING;
                """,
                month_rows,
            )

            cursor.executemany(fact_upsert, fact_rows)

            # Compare every loaded base value with its saved input.
            cursor.execute(
                f"""
                SELECT {column_names}
                FROM public.fact_property_month
                WHERE month BETWEEN %s AND %s;
                """,
                (date(2026, 1, 1), date(2026, 6, 1)),
            )
            stored_rows = cursor.fetchall()

            expected = {
                (row[0], row[1]): row
                for row in fact_rows
            }
            stored = {
                (row[0], row[1]): row
                for row in stored_rows
            }

            if stored != expected:
                raise ValueError(
                    "PostgreSQL values do not match curated inputs."
                )

            # Record total table counts as rerun evidence.
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM public.dim_property),
                    (SELECT COUNT(*) FROM public.dim_month),
                    (SELECT COUNT(*) FROM public.fact_property_month);
                """
            )
            property_count, month_count, fact_count = cursor.fetchone()

    return {
        "properties": property_count,
        "months": month_count,
        "facts": fact_count,
        "reconciled_input_rows": len(fact_rows),
    }


def main():
    """Allow secure execution from the project terminal."""
    from getpass import getpass

    parameters = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": "proptech360",
        "user": os.getenv("PGUSER", "postgres"),
        "password": getpass("PostgreSQL password: "),
        "connect_timeout": 10,
    }

    try:
        print(load_postgres(parameters))
    finally:
        parameters.pop("password", None)


if __name__ == "__main__":
    main()
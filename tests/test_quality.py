"""Test quality rules using small, independent synthetic records."""

import pandas as pd

from src.quality import (
    NUMERIC_RULES,
    separate_duplicates,
    separate_invalid_foreign_keys,
    validate_numeric_fields,
    validate_dates_and_categories,
)


def test_exact_duplicate():
    """Keep one exact copy and quarantine the additional row."""
    source = pd.DataFrame({
        "unit_id": ["U001", "U001"],
        "floor_area_sqm": ["75", "75"],
    })

    accepted, rejected = separate_duplicates(
        source, "unit_id", "test-run"
    )

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected.iloc[0]["reason_code"] == "EXACT_DUPLICATE"


def test_conflicting_identifier():
    """Reject all records when the same ID has different values."""
    source = pd.DataFrame({
        "unit_id": ["U001", "U001"],
        "floor_area_sqm": ["75", "90"],
    })

    accepted, rejected = separate_duplicates(
        source, "unit_id", "test-run"
    )

    assert accepted.empty
    assert len(rejected) == 2
    assert rejected["reason_code"].eq("CONFLICTING_IDENTIFIER").all()


def test_unknown_foreign_key():
    """Retain known references and quarantine unknown references."""
    source = pd.DataFrame({
        "work_order_id": ["WO001", "WO002"],
        "unit_id": ["U001", "UNKNOWN"],
    })

    accepted, rejected = separate_invalid_foreign_keys(
        source,
        "unit_id",
        {"U001"},
        "UNKNOWN_UNIT",
        "test-run",
    )

    assert accepted["work_order_id"].tolist() == ["WO001"]
    assert rejected["work_order_id"].tolist() == ["WO002"]
    assert rejected.iloc[0]["reason_code"] == "UNKNOWN_UNIT"


def test_negative_energy_and_zero():
    """Reject negative energy while retaining a legitimate zero."""
    source = pd.DataFrame({
        "reading_id": ["R001", "R002"],
        "energy_kwh": ["-45", "0"],
    })

    accepted, rejected = validate_numeric_fields(
        source, NUMERIC_RULES["meter_readings"], "test-run"
    )

    assert accepted["reading_id"].tolist() == ["R002"]
    assert accepted.iloc[0]["energy_kwh"] == 0
    assert rejected.iloc[0]["energy_kwh"] == "-45"
    assert "NEGATIVE_ENERGY_KWH" in rejected.iloc[0]["reason_code"]


def test_missing_resolved_response():
    """Retain a resolved order without a response but exclude it from SLA."""
    source = pd.DataFrame({
        "work_order_id": ["WO001"],
        "opened_date": ["2026-01-01"],
        "closed_date": ["2026-01-02"],
        "priority": ["HIGH"],
        "response_hours": [""],
        "cost_usd": ["100"],
        "status": ["RESOLVED"],
    })

    numeric, numeric_rejects = validate_numeric_fields(
        source, NUMERIC_RULES["work_orders"], "test-run"
    )
    accepted, rejected = validate_dates_and_categories(
        numeric, "work_orders", "test-run"
    )

    assert numeric_rejects.empty
    assert rejected.empty
    assert len(accepted) == 1
    assert bool(accepted.iloc[0]["missing_sla_response"])
    assert not bool(accepted.iloc[0]["sla_eligible"])


def test_impossible_date():
    """Reject a date that matches the text format but cannot exist."""
    source = pd.DataFrame({
        "lease_id": ["L001"],
        "start_date": ["2026-02-30"],
        "end_date": [""],
    })

    accepted, rejected = validate_dates_and_categories(
        source, "leases", "test-run"
    )

    assert accepted.empty
    assert "INVALID_START_DATE" in rejected.iloc[0]["reason_code"]


def test_lease_end_before_start():
    """Reject a lease whose end date precedes its start date."""
    source = pd.DataFrame({
        "lease_id": ["L001"],
        "start_date": ["2026-03-10"],
        "end_date": ["2026-03-01"],
    })

    accepted, rejected = validate_dates_and_categories(
        source, "leases", "test-run"
    )

    assert accepted.empty
    assert "LEASE_END_BEFORE_START" in rejected.iloc[0]["reason_code"]
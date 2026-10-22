"""Automated tests for source ingestion."""

import pytest
import pandas as pd

from src.ingest import EXPECTED_COLUMNS, ingest_source


def test_missing_file(tmp_path):
    """A nonexistent source must raise a descriptive error."""
    with pytest.raises(FileNotFoundError, match="Source file not found"):
        ingest_source(
            tmp_path / "missing.csv",
            EXPECTED_COLUMNS["properties.csv"],
        )


@pytest.mark.parametrize(
    "content, expected_message",
    [
        ("", "Source file is empty"),
        (
            "property_id,property_name,city\n",
            "contains no data rows",
        ),
        (
            "property_id\nP001\n",
            "missing required columns",
        ),
    ],
)
def test_invalid_inputs(tmp_path, content, expected_message):
    """Reject empty, header-only, and incomplete CSV inputs."""
    source = tmp_path / "properties.csv"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=expected_message):
        ingest_source(
            source,
            EXPECTED_COLUMNS["properties.csv"],
        )


def test_repeatable_ingestion_preserves_source(tmp_path):
    """Repeated ingestion must preserve source bytes and loaded values."""
    source = tmp_path / "properties.csv"
    source.write_text(
        "property_id,property_name,city\n"
        "P001,Test Property,Lagos\n",
        encoding="utf-8",
    )
    original_bytes = source.read_bytes()

    first_df, first_audit = ingest_source(
        source, EXPECTED_COLUMNS["properties.csv"]
    )
    second_df, second_audit = ingest_source(
        source, EXPECTED_COLUMNS["properties.csv"]
    )

    assert source.read_bytes() == original_bytes
    assert first_audit["sha256"] == second_audit["sha256"]
    assert first_audit["row_count"] == 1
    pd.testing.assert_frame_equal(first_df, second_df)
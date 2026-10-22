"""Reusable CSV ingestion and audit functions for Capstone Project."""

from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json

import pandas as pd


# Resolve paths from this module's location rather than the terminal's folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
MANIFEST_PATH = PROJECT_ROOT / "data" / "curated" / "ingestion_manifest.json"

# Required source columns. Additional columns are preserved.
EXPECTED_COLUMNS = {
    "properties.csv": {
        "property_id",
        "property_name",
        "city",
    },
    "units.csv": {
        "unit_id",
        "property_id",
        "floor_area_sqm",
        "active",
    },
    "leases.csv": {
        "lease_id",
        "unit_id",
        "start_date",
        "end_date",
        "monthly_rent_usd",
    },
    "work_orders.csv": {
        "work_order_id",
        "unit_id",
        "opened_date",
        "closed_date",
        "priority",
        "response_hours",
        "cost_usd",
        "status",
    },
    "meter_readings.csv": {
        "reading_id",
        "unit_id",
        "reading_month",
        "energy_kwh",
    },
}


def ingest_source(
    path: Path,
    expected_columns: set[str],
) -> tuple[pd.DataFrame, dict]:
    """
    Validate and load one CSV without modifying the source file.

    Returns:
        A DataFrame and its ingestion audit entry.

    Raises:
        FileNotFoundError: The source file does not exist.
        ValueError: The file is empty, unreadable, or missing required columns.
        OSError: The operating system cannot read the file.
    """
    path = Path(path)

    # Reject missing files and paths that refer to directories.
    if not path.is_file():
        raise FileNotFoundError(f"Source file not found: {path}")

    # Read once so the DataFrame and hash describe the same source bytes.
    source_bytes = path.read_bytes()

    if not source_bytes.strip():
        raise ValueError(f"Source file is empty: {path.name}")

    try:
        # Preserve source values as text for validation in the cleaning stage.
        # Empty fields remain empty strings rather than automatic NaN values.
        df = pd.read_csv(
            BytesIO(source_bytes),
            dtype="string",
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
        UnicodeDecodeError,
    ) as exc:
        raise ValueError(
            f"Cannot read CSV {path.name}: {exc}"
        ) from exc

    # Compare the required columns with the actual CSV headers.
    missing_columns = expected_columns - set(df.columns)

    if missing_columns:
        raise ValueError(
            f"{path.name} is missing required columns: "
            f"{', '.join(sorted(missing_columns))}"
        )

    # Reject a file that contains headers but no records.
    if df.empty:
        raise ValueError(
            f"Source file contains no data rows: {path.name}"
        )

    # Record when ingestion happened and fingerprint the original contents.
    manifest_entry = {
        "filename": path.name,
        "row_count": len(df),
        "ingest_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
    }

    return df, manifest_entry


def ingest_all(
    raw_dir: Path = RAW_DIR,
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """
    Load all five sources and save their audit manifest.

    Repeated calls replace the manifest rather than append duplicate entries.
    Raw source files remain unchanged.
    """
    raw_dir = Path(raw_dir)
    manifest_path = Path(manifest_path)

    # Prevent an accidentally supplied output path from replacing a raw file.
    if manifest_path.resolve().is_relative_to(raw_dir.resolve()):
        raise ValueError("The manifest must be saved outside the raw folder.")

    datasets = {}
    ingestion_manifest = []

    # Validate every source before writing the manifest.
    for filename, expected_columns in EXPECTED_COLUMNS.items():
        df, entry = ingest_source(
            raw_dir / filename,
            expected_columns,
        )

        # Use names such as "properties" and "leases" as dictionary keys.
        datasets[Path(filename).stem] = df
        ingestion_manifest.append(entry)

    # Create the output folder if it does not already exist.
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    # Overwrite the previous manifest to keep repeated runs safe.
    manifest_path.write_text(
        json.dumps(ingestion_manifest, indent=2),
        encoding="utf-8",
    )

    return datasets, ingestion_manifest


def main() -> None:
    """Run ingestion when this file is executed directly."""
    datasets, ingestion_manifest = ingest_all()

    for entry in ingestion_manifest:
        print(f"{entry['filename']}: {entry['row_count']:,} rows")

    print(f"Audit manifest saved to: {MANIFEST_PATH}")


# Importing this module does not automatically run ingestion.
if __name__ == "__main__":
    main()
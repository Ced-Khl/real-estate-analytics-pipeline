"""Reusable data-quality rules and functions for PropTech 360.

Functions do not modify their input DataFrames.

Quarantine policy:
- Exact duplicates: retain the first occurrence.
- Conflicting identifiers: reject every affected row.
- Invalid foreign keys, numbers, dates or categories: quarantine.
- Missing lease end dates: retain as ongoing leases.
- Resolved orders missing response hours: retain and flag;
  exclude from the SLA-eligible denominator.

Run these validations in the notebook before the overlap checks,
reconciliation and output-saving cells.
"""

import numpy as np
import pandas as pd


# Primary identifier for each source dataset.
IDENTIFIERS = {
    "properties": "property_id",
    "units": "unit_id",
    "leases": "lease_id",
    "work_orders": "work_order_id",
    "meter_readings": "reading_id",
}


# Numeric validation rules.
# "positive": greater than zero.
# "nonnegative": zero or greater.
# "binary": either 0 or 1.
NUMERIC_RULES = {
    "properties": {},
    "units": {
        "floor_area_sqm": {
            "rule": "positive",
            "allow_blank": False,
        },
        "active": {
            "rule": "binary",
            "allow_blank": False,
        },
    },
    "leases": {
        "monthly_rent_usd": {
            "rule": "nonnegative",
            "allow_blank": False,
        },
    },
    "work_orders": {
        "response_hours": {
            "rule": "nonnegative",
            "allow_blank": True,
        },
        "cost_usd": {
            "rule": "nonnegative",
            "allow_blank": False,
        },
    },
    "meter_readings": {
        "energy_kwh": {
            "rule": "nonnegative",
            "allow_blank": False,
        },
    },
}


def profile_table(df: pd.DataFrame, identifier: str) -> dict:
    """Summarise missing values, duplicates and column types."""

    if identifier not in df.columns:
        raise ValueError(
            f"Identifier column not found: {identifier}"
        )

    # Blank text and actual pandas nulls are counted separately.
    blank_counts = {
        column: int(
            df[column]
            .astype("string")
            .str.strip()
            .eq("")
            .fillna(False)
            .sum()
        )
        for column in df.columns
    }

    return {
        "row_count": len(df),
        "nulls_by_column": {
            column: int(count)
            for column, count in df.isna().sum().items()
        },
        "blanks_by_column": blank_counts,
        # Count additional occurrences after the first identifier.
        "duplicate_identifier_count": int(
            df.duplicated(
                subset=[identifier],
                keep="first",
            ).sum()
        ),
        # Count additional rows identical across all columns.
        "exact_duplicate_count": int(
            df.duplicated(keep="first").sum()
        ),
        "types": {
            column: str(dtype)
            for column, dtype in df.dtypes.items()
        },
    }


def separate_duplicates(
    df: pd.DataFrame,
    identifier: str,
    run_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate exact duplicates and conflicting identifiers."""

    if identifier not in df.columns:
        raise ValueError(
            f"Identifier column not found: {identifier}"
        )

    source = df.copy().reset_index(drop=True)

    # Remove exact copies temporarily to identify IDs associated
    # with genuinely different records.
    distinct_rows = source.drop_duplicates()

    conflicting_ids = distinct_rows.loc[
        distinct_rows.duplicated(
            subset=[identifier],
            keep=False,
        ),
        identifier,
    ]

    # Reject all occurrences of an identifier with conflicting values.
    conflict_mask = source[identifier].isin(conflicting_ids)

    # Otherwise keep the first exact copy and reject later copies.
    exact_duplicate_mask = (
        source.duplicated(keep="first")
        & ~conflict_mask
    )

    reject_mask = conflict_mask | exact_duplicate_mask

    accepted = source.loc[~reject_mask].copy()
    rejected = source.loc[reject_mask].copy()

    rejected["reason_code"] = [
        "CONFLICTING_IDENTIFIER"
        if conflict_mask.loc[index]
        else "EXACT_DUPLICATE"
        for index in rejected.index
    ]
    rejected["run_id"] = run_id

    assert len(source) == len(accepted) + len(rejected)

    return (
        accepted.reset_index(drop=True),
        rejected.reset_index(drop=True),
    )


def separate_invalid_foreign_keys(
    df: pd.DataFrame,
    foreign_key: str,
    valid_parent_ids: set,
    reason_code: str,
    run_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reject records whose foreign key has no accepted parent."""

    if foreign_key not in df.columns:
        raise ValueError(
            f"Foreign-key column not found: {foreign_key}"
        )

    source = df.copy()

    # Missing or unknown references cannot match an accepted parent.
    invalid_mask = ~source[foreign_key].isin(valid_parent_ids)

    accepted = source.loc[~invalid_mask].copy()
    rejected = source.loc[invalid_mask].copy()

    rejected["reason_code"] = reason_code
    rejected["run_id"] = run_id

    assert len(source) == len(accepted) + len(rejected)

    return (
        accepted.reset_index(drop=True),
        rejected.reset_index(drop=True),
    )


def validate_numeric_fields(
    df: pd.DataFrame,
    rules: dict,
    run_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate numeric fields and convert accepted values.

    Rejected rows retain their values as supplied to this function.
    Multiple failures are recorded in one semicolon-separated field.
    """

    source = df.copy().reset_index(drop=True)
    converted = source.copy()

    # Separate lists avoid sharing failure reasons between rows.
    reasons = [[] for _ in range(len(source))]

    for column, settings in rules.items():
        if column not in source.columns:
            raise ValueError(
                f"Numeric column not found: {column}"
            )

        rule = settings["rule"]

        if rule not in {"positive", "nonnegative", "binary"}:
            raise ValueError(
                f"Unknown numeric rule for {column}: {rule}"
            )

        text = source[column].astype("string").str.strip()
        blank = text.isna() | text.eq("").fillna(False)

        # Invalid numeric text becomes missing for validation.
        numbers = pd.to_numeric(
            text.mask(blank),
            errors="coerce",
        ).astype("Float64")

        # Reject NaN, infinity and negative infinity when supplied
        # as nonblank source values.
        finite = pd.Series(
            np.isfinite(
                numbers.to_numpy(
                    dtype=float,
                    na_value=np.nan,
                )
            ),
            index=source.index,
        )

        # Use "not" for a scalar Boolean and "~" for a Boolean Series.
        missing_required = (
            blank & (not settings["allow_blank"])
        )
        invalid_number = ~blank & ~finite

        checks = {
            f"MISSING_{column.upper()}": missing_required,
            f"INVALID_NUMBER_{column.upper()}": invalid_number,
        }

        # Range checks apply only to finite values.
        if rule == "positive":
            checks[f"NONPOSITIVE_{column.upper()}"] = (
                finite & numbers.le(0).fillna(False)
            )

        elif rule == "nonnegative":
            checks[f"NEGATIVE_{column.upper()}"] = (
                finite & numbers.lt(0).fillna(False)
            )

        elif rule == "binary":
            checks[f"INVALID_{column.upper()}"] = (
                finite & ~numbers.isin([0, 1])
            )

        for reason_code, mask in checks.items():
            for index in source.index[mask]:
                reasons[index].append(reason_code)

        # Conversion affects only the separate working DataFrame.
        converted[column] = numbers

    reject_mask = pd.Series(
        [bool(row_reasons) for row_reasons in reasons],
        index=source.index,
        dtype=bool,
    )

    accepted = converted.loc[~reject_mask].copy()
    rejected = source.loc[reject_mask].copy()

    rejected["reason_code"] = [
        ";".join(reasons[index])
        for index in rejected.index
    ]
    rejected["run_id"] = run_id

    # Store the validated active flag as a nullable integer.
    if "active" in rules:
        accepted["active"] = accepted["active"].astype("Int64")

    assert len(source) == len(accepted) + len(rejected)

    return (
        accepted.reset_index(drop=True),
        rejected.reset_index(drop=True),
    )


def validate_dates_and_categories(
    df: pd.DataFrame,
    dataset_name: str,
    run_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate dates, chronology, priorities and statuses.

    Call this after numeric validation so missing response durations
    are represented as pandas missing values.

    Accepted date columns become datetime values.
    Rejected rows retain the values supplied to this function.
    """

    if dataset_name not in IDENTIFIERS:
        raise ValueError(
            f"Unknown dataset: {dataset_name}"
        )

    source = df.copy().reset_index(drop=True)
    converted = source.copy()
    reasons = [[] for _ in range(len(source))]

    def flag(mask: pd.Series, reason_code: str) -> None:
        """Attach a reason to each row matching a Boolean mask."""
        mask = mask.fillna(False)

        for index in source.index[mask]:
            reasons[index].append(reason_code)

    def parse_date(column: str, required: bool) -> pd.Series:
        """Parse strict YYYY-MM-DD source dates."""
        text = source[column].astype("string").str.strip()
        blank = text.isna() | text.eq("").fillna(False)

        correct_format = text.str.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            na=False,
        )

        parsed = pd.to_datetime(
            text.where(~blank & correct_format),
            format="%Y-%m-%d",
            errors="coerce",
        )

        if required:
            flag(
                blank,
                f"MISSING_{column.upper()}",
            )

        # Catch malformed text and impossible calendar dates.
        flag(
            ~blank & parsed.isna(),
            f"INVALID_{column.upper()}",
        )

        converted[column] = parsed
        return parsed

    if dataset_name == "leases":
        start = parse_date("start_date", required=True)
        end = parse_date("end_date", required=False)

        # Blank end dates are valid for ongoing leases.
        flag(
            start.notna()
            & end.notna()
            & start.gt(end),
            "LEASE_END_BEFORE_START",
        )

    elif dataset_name == "work_orders":
        opened = parse_date("opened_date", required=True)
        closed = parse_date("closed_date", required=False)

        # Reject unrecognised categories without silently correcting them.
        priority = source["priority"]
        status = source["status"]

        flag(
            ~priority.isin(
                ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
            ),
            "INVALID_PRIORITY",
        )
        flag(
            ~status.isin(["OPEN", "RESOLVED"]),
            "INVALID_STATUS",
        )

        resolved = status.eq("RESOLVED")
        open_order = status.eq("OPEN")

        flag(
            resolved & closed.isna(),
            "RESOLVED_WITHOUT_VALID_CLOSED_DATE",
        )
        flag(
            open_order & closed.notna(),
            "OPEN_WITH_CLOSED_DATE",
        )
        flag(
            opened.notna()
            & closed.notna()
            & opened.gt(closed),
            "CLOSED_BEFORE_OPENED",
        )

        # Numeric validation has already converted blanks to missing values.
        missing_response = source["response_hours"].isna()

        # Retain resolved orders with missing responses for volume reporting.
        converted["missing_sla_response"] = (
            resolved & missing_response
        )

        # Only resolved orders with a known response enter the denominator.
        converted["sla_eligible"] = (
            resolved & ~missing_response
        )

    elif dataset_name == "meter_readings":
        month = parse_date("reading_month", required=True)

        # The source contract uses month-start dates for Jan–Jun 2026.
        flag(
            month.notna()
            & ~month.between(
                "2026-01-01",
                "2026-06-01",
            ),
            "READING_MONTH_OUT_OF_RANGE",
        )
        flag(
            month.notna()
            & month.dt.day.ne(1),
            "READING_MONTH_NOT_MONTH_START",
        )

    # Properties and units have no date or category fields handled here.
    reject_mask = pd.Series(
        [bool(row_reasons) for row_reasons in reasons],
        index=source.index,
        dtype=bool,
    )

    accepted = converted.loc[~reject_mask].copy()
    rejected = source.loc[reject_mask].copy()

    rejected["reason_code"] = [
        ";".join(reasons[index])
        for index in rejected.index
    ]
    rejected["run_id"] = run_id

    assert len(source) == len(accepted) + len(rejected)

    return (
        accepted.reset_index(drop=True),
        rejected.reset_index(drop=True),
    )
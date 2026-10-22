"""Validate raw CSV arrival metadata and write S3 audit records."""

import hashlib
import json
import logging
import os
from urllib.parse import unquote_plus

import boto3


s3 = boto3.client("s3")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RAW_BUCKET = os.environ["RAW_BUCKET"]
AUDIT_BUCKET = os.environ["AUDIT_BUCKET"]


def log_event(action, **details):
    """Emit structured JSON logs for CloudWatch."""
    logger.info(json.dumps({"action": action, **details}))


def process_record(record):
    """Validate one S3 notification and persist its audit record."""

    # Ignore notifications that are not S3 object-creation events.
    if (
        record.get("eventSource") != "aws:s3"
        or not record.get("eventName", "").startswith("ObjectCreated:")
    ):
        log_event("ignored", reason="unsupported_event")
        return "ignored"

    bucket = record["s3"]["bucket"]["name"]
    object_info = record["s3"]["object"]

    # S3 notification keys are URL encoded; decode spaces and special symbols.
    key = unquote_plus(object_info["key"])

    # Restrict processing to the intended bucket and raw CSV prefix.
    # Audit writes are outside raw/, preventing recursive processing.
    if (
        bucket != RAW_BUCKET
        or not key.startswith("raw/")
        or not key.lower().endswith(".csv")
    ):
        log_event("ignored", bucket=bucket, key=key)
        return "ignored"

    event_etag = str(object_info.get("eTag", "")).strip('"')
    sequencer = object_info.get("sequencer")

    if not event_etag or not sequencer:
        raise ValueError("S3 notification is missing eTag or sequencer.")

    # Deterministic identity: duplicate deliveries reuse the same audit key.
    # A separate upload has its own sequencer, even with identical contents.
    identity = json.dumps(
        [bucket, key, event_etag, sequencer],
        separators=(",", ":"),
    )
    audit_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    audit_key = f"audit/raw-arrivals/{audit_id}.json"

    # Read object metadata without downloading or processing the CSV.
    # This handler is designed for our versioning-disabled learning bucket.
    metadata = s3.head_object(Bucket=bucket, Key=key)

    current_etag = metadata["ETag"].strip('"')
    current_size = metadata["ContentLength"]

    status = "ACCEPTED_METADATA"
    reasons = []

    # An unversioned object may have been overwritten before processing.
    # Do not claim that current metadata describes an older notification.
    if current_etag != event_etag:
        status = "SUPERSEDED"
        reasons.append("OBJECT_CHANGED_SINCE_EVENT")
    else:
        if current_size == 0:
            reasons.append("EMPTY_FILE")

        if current_size != object_info.get("size"):
            reasons.append("EVENT_SIZE_MISMATCH")

        if reasons:
            status = "REJECTED_METADATA"

    audit = {
        "audit_id": audit_id,
        "source_bucket": bucket,
        "source_key": key,
        "event_name": record["eventName"],
        "event_time": record.get("eventTime"),
        "event_etag": event_etag,
        "sequencer": sequencer,
        "event_size_bytes": object_info.get("size"),
        "observed_etag": current_etag,
        "observed_size_bytes": current_size,
        "observed_last_modified_utc": metadata["LastModified"].isoformat(),
        "content_type": metadata.get("ContentType"),
        "server_side_encryption": metadata.get("ServerSideEncryption"),
        "status": status,
        "reason_codes": reasons,
        "validation_scope": "Arrival metadata only; CSV content not validated.",
    }

    # Overwrite the deterministic key on duplicate delivery.
    # This prevents duplicate audit objects; invocation logs may still repeat.
    s3.put_object(
        Bucket=AUDIT_BUCKET,
        Key=audit_key,
        Body=json.dumps(audit, indent=2).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )

    log_event(
        "audit_written",
        source_key=key,
        audit_key=audit_key,
        status=status,
        reason_codes=reasons,
    )

    return status


def lambda_handler(event, context):
    """Process all records and propagate operational failures for retries."""

    # S3's configuration test message does not contain a Records array.
    if event.get("Event") == "s3:TestEvent":
        log_event("s3_test_event")
        return {"message": "S3 test event received"}

    records = event.get("Records")

    if not isinstance(records, list) or not records:
        raise ValueError("Expected a nonempty S3 Records list.")

    results = []
    failures = []

    for index, record in enumerate(records):
        try:
            results.append(process_record(record))
        except Exception:
            # Attempt remaining records, but do not hide failed operations.
            logger.exception(json.dumps({
                "action": "record_failed",
                "record_index": index,
                "request_id": getattr(context, "aws_request_id", None),
            }))
            failures.append(index)

    if failures:
        # Direct S3 invocation retries the invocation, not individual records.
        # Successful records can be replayed safely using deterministic keys.
        raise RuntimeError(f"Failed S3 record indices: {failures}")

    return {
        "processed_records": len(records),
        "results": results,
    }
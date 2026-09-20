"""
Step 1 of the SIPCA pipeline.

Connects to the Azure Blob Storage container "sipca", folder "raw",
and downloads event_index.json to a local path for processing by
02_sipca_review.py.

Credentials come from config.ini (never hardcoded here). Supports either:
  - a full connection string, or
  - an account URL + SAS token

Freshness check
---------------
Before downloading, the script reads the blob's last-modified time in Azure
and stops with a clear error if the file is older than allowed. This prevents
the review from silently running on old data when the upstream job that
writes event_index.json has not run.

  [azure]
  ; 0 = must have been updated today, 1 = today or yesterday, etc.
  ; off = disable the check
  max_age_days = 0
  ; decides what "today" means, e.g. Africa/Nairobi (default UTC)
  freshness_timezone = UTC

Usage:
    python 01_download_event_index.py [--config config.ini] [--allow-stale]

Exit codes:
    0 = success
    1 = config / credential problem
    2 = download failure (network, missing blob, etc.) OR stale blob
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from common import load_config, setup_logging

try:
    from azure.storage.blob import BlobServiceClient
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    BlobServiceClient = None  # handled below with a clear error message
    ResourceNotFoundError = Exception


def get_blob_client(cfg, logger):
    if BlobServiceClient is None:
        logger.error(
            "azure-storage-blob is not installed. Run: pip install -r requirements.txt"
        )
        sys.exit(1)

    azure_cfg = cfg["azure"]
    connection_string = azure_cfg.get("connection_string", "").strip()
    account_url = azure_cfg.get("account_url", "").strip()
    sas_token = azure_cfg.get("sas_token", "").strip()
    container_name = azure_cfg.get("container_name", "sipca").strip()
    folder_path = azure_cfg.get("folder_path", "raw").strip().strip("/")
    blob_name = azure_cfg.get("blob_name", "event_index.json").strip()

    if connection_string:
        service_client = BlobServiceClient.from_connection_string(connection_string)
    elif account_url and sas_token:
        service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    else:
        logger.error(
            "No valid Azure credentials found in config.ini. "
            "Set either [azure] connection_string, or account_url + sas_token."
        )
        sys.exit(1)

    full_blob_path = f"{folder_path}/{blob_name}" if folder_path else blob_name
    blob_client = service_client.get_blob_client(container=container_name, blob=full_blob_path)
    return blob_client, full_blob_path, container_name


def _get_timezone(name, logger):
    """Return a tzinfo for the configured name; fall back to UTC with a warning."""
    name = (name or "UTC").strip()
    if name.upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:
        logger.warning(
            f"Could not load timezone '{name}' ({e}); using UTC instead. "
            "On Windows you may need: pip install tzdata"
        )
        return timezone.utc


def check_freshness(props, full_blob_path, container_name, azure_cfg, logger):
    """Exit with code 2 if the blob in Azure is older than max_age_days."""
    raw = azure_cfg.get("max_age_days", "0").strip().lower()
    if raw in ("off", "none", "false", "-1"):
        logger.warning("Freshness check is disabled (max_age_days = off).")
        return

    try:
        max_age_days = int(raw) if raw else 0
    except ValueError:
        logger.error(f"[azure] max_age_days must be a whole number or 'off', got: '{raw}'")
        sys.exit(1)

    tz = _get_timezone(azure_cfg.get("freshness_timezone", "UTC"), logger)
    modified = props.last_modified.astimezone(tz)
    today = datetime.now(tz).date()
    age_days = (today - modified.date()).days
    tz_label = modified.tzname() or str(tz)

    logger.info(
        f"Blob last modified in Azure: {modified:%Y-%m-%d %H:%M} {tz_label} "
        f"({age_days} day(s) before today, {today})"
    )

    if age_days > max_age_days:
        expected = "today" if max_age_days == 0 else f"within the last {max_age_days} day(s)"
        logger.error(
            f"STALE DATA: '{full_blob_path}' in container '{container_name}' was last "
            f"updated in Azure on {modified:%Y-%m-%d %H:%M} {tz_label} "
            f"({age_days} day(s) ago), but it should have been updated {expected} "
            f"(today is {today}). The download itself works, so the upstream job that "
            "writes this file has probably not run. Contact the OCBA datalake team. "
            "The pipeline was stopped so the review does not run on old data."
        )
        sys.exit(2)


def download(cfg, logger, allow_stale=False):
    blob_client, full_blob_path, container_name = get_blob_client(cfg, logger)
    azure_cfg = cfg["azure"]

    local_path = azure_cfg.get("local_download_path", "./data/event_index.json").strip()
    local_dir = os.path.dirname(local_path)
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)

    logger.info(f"Checking '{full_blob_path}' in container '{container_name}'")

    try:
        try:
            props = blob_client.get_blob_properties()
        except ResourceNotFoundError:
            logger.error(f"Blob not found: {full_blob_path} in container {container_name}")
            sys.exit(2)

        if allow_stale:
            logger.warning("--allow-stale given: skipping the freshness check.")
        else:
            check_freshness(props, full_blob_path, container_name, azure_cfg, logger)

        logger.info(f"Downloading -> {local_path}")
        with open(local_path, "wb") as f:
            stream = blob_client.download_blob()
            stream.readinto(f)
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"Failed to download blob: {e}")
        sys.exit(2)

    size = os.path.getsize(local_path)
    logger.info(f"[✓] Download complete: {local_path} ({size:,} bytes)")
    return local_path


def main():
    parser = argparse.ArgumentParser(description="Download event_index.json from Azure Blob Storage")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Download even if the blob is older than max_age_days (for testing/backfills)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_dir = cfg["paths"].get("log_dir", "./logs") if cfg.has_section("paths") else "./logs"
    logger = setup_logging(log_dir, "download")

    try:
        download(cfg, logger, allow_stale=args.allow_stale)
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during download: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

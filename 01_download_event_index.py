"""
Step 1 of the SIPCA pipeline.

Connects to the Azure Blob Storage container "sipca", folder "raw",
and downloads event_index.json to a local path for processing by
02_sipca_review.py.

Credentials come from config.ini (never hardcoded here). Supports either:
  - a full connection string, or
  - an account URL + SAS token

Usage:
    python 01_download_event_index.py [--config config.ini]

Exit codes:
    0 = success
    1 = config / credential problem
    2 = download failure (network, missing blob, etc.)
"""

import argparse
import os
import sys

from common import load_config, setup_logging

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    BlobServiceClient = None  # handled below with a clear error message


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


def download(cfg, logger):
    blob_client, full_blob_path, container_name = get_blob_client(cfg, logger)
    local_path = cfg["azure"].get("local_download_path", "./data/event_index.json").strip()

    local_dir = os.path.dirname(local_path)
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)

    logger.info(f"Downloading '{full_blob_path}' from container '{container_name}' -> {local_path}")

    try:
        if not blob_client.exists():
            logger.error(f"Blob not found: {full_blob_path} in container {container_name}")
            sys.exit(2)

        with open(local_path, "wb") as f:
            stream = blob_client.download_blob()
            stream.readinto(f)

    except Exception as e:
        logger.error(f"Failed to download blob: {e}")
        sys.exit(2)

    size = os.path.getsize(local_path)
    logger.info(f"[✓] Download complete: {local_path} ({size:,} bytes)")
    return local_path


def main():
    parser = argparse.ArgumentParser(description="Download event_index.json from Azure Blob Storage")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_dir = cfg["paths"].get("log_dir", "./logs") if cfg.has_section("paths") else "./logs"
    logger = setup_logging(log_dir, "download")

    try:
        download(cfg, logger)
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during download: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

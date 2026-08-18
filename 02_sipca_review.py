"""
Step 2 of the SIPCA pipeline.

Classifies records in event_index.json by transfer status, tracks the
deleted_dhis2 field, and generates a timestamped weekly summary report.

This is a config-driven version of the original review script:
  - Paths come from config.ini instead of being hardcoded
  - Writes a small status JSON (action count, report path) that
    03_send_email.py reads, so the two scripts stay decoupled

Usage:
    python 02_sipca_review.py [--config config.ini]

Exit codes:
    0 = success
    1 = config / input file problem
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from common import load_config, setup_logging


# STEP 1: Load event_index.json
def load_event_index(path, logger):
    if not os.path.exists(path):
        logger.error(f"event_index.json not found at {path}. Did the download step run?")
        sys.exit(1)
    with open(path, "r") as f:
        return json.load(f)


# STEP 2: Classify records + handle deleted_dhis2 field
def classify_records(data):
    """
    Classifies each record and ensures the deleted_dhis2 field exists.

    deleted_dhis2 field logic:
      - Only relevant when deleted=true AND uploaded=true
        (i.e. deleted in Kobo but exists in DHIS2)
      - If deleted_dhis2 is not present, it is initialized to False
      - When you manually delete from DHIS2, set deleted_dhis2 = True
        to stop it from appearing in future action-required reports
    """
    action_required = {}
    no_action = {}
    already_handled = {}

    for record_id, record in data.items():
        deleted = record.get("deleted", False)
        uploaded = record.get("uploaded", False)

        if "deleted_dhis2" not in record:
            record["deleted_dhis2"] = False

        deleted_dhis2 = record["deleted_dhis2"]

        if not deleted and not uploaded:
            record["status"] = "NOT_UPLOADED_TO_DHIS2"
            action_required[record_id] = record

        elif deleted and uploaded and not deleted_dhis2:
            record["status"] = "DELETED_IN_KOBO_BUT_EXISTS_IN_DHIS2"
            action_required[record_id] = record

        elif deleted and uploaded and deleted_dhis2:
            record["status"] = "DELETED_IN_KOBO_AND_DHIS2"
            already_handled[record_id] = record

        else:
            no_action[record_id] = record

    return action_required, no_action, already_handled


# STEP 3: Save updated event_index.json (with deleted_dhis2)
def save_event_index(path, data, logger):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"[✓] event_index.json updated with deleted_dhis2 field -> {path}")


# STEP 4: Build report text
def build_report(data, action_required, no_action, already_handled, run_ts):
    lines = []
    lines.append("=" * 60)
    lines.append("  SIPCA Tasks Review Report — Summary of Event Index Status")
    lines.append(f"  Run timestamp : {run_ts}")
    lines.append("=" * 60)

    lines.append("\n── SUMMARY ──────────────────────────────────────────────")
    lines.append(f"  Total records      : {len(data)}")
    lines.append(f"  No action needed   : {len(no_action)}")
    lines.append(f"  Already handled    : {len(already_handled)}  (deleted in Kobo & DHIS2)")
    lines.append(f"  Action required    : {len(action_required)}")

    if action_required:
        lines.append("\n── RECORDS REQUIRING ACTION ─────────────────────────────")
        for rid, record in action_required.items():
            status = record.get("status", "UNKNOWN")
            lines.append(f"\n  ID        : {rid}")
            lines.append(f"  Status    : {status}")
            lines.append(f"  deleted   : {record.get('deleted')}")
            lines.append(f"  uploaded  : {record.get('uploaded')}")
            lines.append(f"  dhis2_uuid: {record.get('dhis2_uuid', 'N/A')}")
            lines.append(f"  deleted_dhis2: {record.get('deleted_dhis2')}")

            if status == "NOT_UPLOADED_TO_DHIS2":
                lines.append("  -> ACTION: Check logs for missing OU mapping, sipca-admin")
                lines.append("             access, or SIPCA IPD program assignment to OU.")
            elif status == "DELETED_IN_KOBO_BUT_EXISTS_IN_DHIS2":
                lines.append("  -> ACTION: Confirm deletion with referent, then delete")
                lines.append(f"             event {record.get('dhis2_uuid')} from DHIS2.")
                lines.append("             After deletion, set deleted_dhis2=true in event_index.json.")
    else:
        lines.append("\n  All records are in order. No action needed this week.")

    if already_handled:
        lines.append("\n── ALREADY HANDLED (deleted in Kobo & DHIS2) ───────────")
        for rid, record in already_handled.items():
            lines.append(f"  ID: {rid}  |  dhis2_uuid: {record.get('dhis2_uuid', 'N/A')}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# STEP 5: Save report to file
def save_report(report_text, run_ts, report_dir, logger):
    os.makedirs(report_dir, exist_ok=True)
    date_str = run_ts[:10]
    filename = f"sipca_report_{date_str}.txt"
    filepath = os.path.join(report_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_text)
    logger.info(f"[✓] Report saved -> {filepath}")
    return filepath


# STEP 6: Write status JSON for 03_send_email.py to consume
def save_status(status_path, run_ts, action_count, no_action_count, already_handled_count, report_path, logger):
    status_dir = os.path.dirname(status_path)
    if status_dir:
        os.makedirs(status_dir, exist_ok=True)
    status = {
        "run_ts": run_ts,
        "action_required": action_count,
        "no_action": no_action_count,
        "already_handled": already_handled_count,
        "report_path": os.path.abspath(report_path),
    }
    with open(status_path, "w") as f:
        json.dump(status, f, indent=2)
    logger.info(f"[✓] Status written -> {status_path}")


def main():
    parser = argparse.ArgumentParser(description="SIPCA event index weekly review")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_dir = cfg["paths"].get("log_dir", "./logs")
    logger = setup_logging(log_dir, "review")

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(f"SIPCA Weekly Review — {run_ts}")

    event_index_path = cfg["azure"].get("local_download_path", "./data/event_index.json")
    report_dir = cfg["paths"].get("report_dir", "./reports")
    status_path = cfg["paths"].get("status_file", "./data/sipca_status.json")

    # 1. Load
    data = load_event_index(event_index_path, logger)

    # 2. Classify (also initialises deleted_dhis2 where missing)
    action_required, no_action, already_handled = classify_records(data)

    # 3. Save updated event_index.json (with deleted_dhis2 fields added)
    save_event_index(event_index_path, data, logger)

    # 4. Build + print report
    report_text = build_report(data, action_required, no_action, already_handled, run_ts)
    print(report_text)

    # 5. Save report file
    report_path = save_report(report_text, run_ts, report_dir, logger)

    # 6. Save status for 03_send_email.py
    save_status(
        status_path, run_ts,
        len(action_required), len(no_action), len(already_handled),
        report_path, logger,
    )

    logger.info(f"Review complete. Action required on {len(action_required)} record(s).")


if __name__ == "__main__":
    main()

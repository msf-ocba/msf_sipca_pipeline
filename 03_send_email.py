"""
Step 3 of the SIPCA pipeline.

Reads the status JSON written by 02_sipca_review.py and, if conditions are
met, emails the report generated in that step. All SMTP credentials and
recipients come from config.ini — nothing is hardcoded here.

(Failure alerts, e.g. "source data is stale", are sent by pipeline.py, not
by this script.)

Usage:
    python 03_send_email.py [--config config.ini]

Exit codes:
    0 = success (email sent, or skipped because disabled/not required)
    1 = config problem
    2 = SMTP send failure
"""

import argparse
import json
import os
import sys

from common import load_config, setup_logging, get_bool, send_smtp_message


def load_status(status_path, logger):
    if not os.path.exists(status_path):
        logger.error(f"Status file not found: {status_path}. Did 02_sipca_review.py run first?")
        sys.exit(1)
    with open(status_path, "r") as f:
        return json.load(f)


def send_email(cfg, status, logger):
    smtp_cfg = cfg["smtp"]

    enabled = get_bool(cfg, "smtp", "enabled", default=False)
    if not enabled:
        logger.info("Email notifications disabled (smtp.enabled=false in config.ini). Skipping.")
        return

    only_if_action = get_bool(cfg, "smtp", "send_only_if_action_required", default=True)
    action_count = status.get("action_required", 0)
    if only_if_action and action_count == 0:
        logger.info("No action required and send_only_if_action_required=true. Skipping email.")
        return

    report_path = status.get("report_path")
    if not report_path or not os.path.exists(report_path):
        logger.error(f"Report file referenced in status.json not found: {report_path}")
        sys.exit(1)

    with open(report_path, "r", encoding="utf-8") as f:
        report_text = f.read()

    # Subject line reflects the actual outcome instead of always saying
    # "Action Required" — configurable per case, with sensible defaults.
    if action_count > 0:
        subject = smtp_cfg.get(
            "subject_action_required",
            f"SIPCA Tasks Review : Action Required ({action_count})",
        )
    else:
        subject = smtp_cfg.get(
            "subject_no_action",
            "SIPCA Tasks Review : All Clear, No Action Required",
        )

    try:
        to_addrs = send_smtp_message(cfg, subject, report_text)
        logger.info(f"[✓] Email notification sent to: {', '.join(to_addrs)}")
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description="Send SIPCA review email notification")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_dir = cfg["paths"].get("log_dir", "./logs")
    logger = setup_logging(log_dir, "email")

    status_path = cfg["paths"].get("status_file", "./data/sipca_status.json")
    status = load_status(status_path, logger)

    send_email(cfg, status, logger)


if __name__ == "__main__":
    main()

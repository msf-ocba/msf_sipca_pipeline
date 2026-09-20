"""
Runs the full SIPCA weekly review pipeline in order:
  1. 01_download_event_index.py  — pull latest event_index.json from Azure
                                   (fails if the blob in Azure is stale)
  2. 02_sipca_review.py          — classify records, build report, write status
  3. 03_send_email.py            — notify recipients if action is required

Designed to be invoked by cron. Includes:
  - A lock file so overlapping cron runs don't collide if one run is slow
  - Clear, timestamped logging to logs/sipca_pipeline.log
  - A failure alert email (when [smtp] is enabled) that explains what went
    wrong, e.g. "source data is stale", so problems are not only in the log
  - Non-zero exit code if any step fails, so cron can alert via its own
    mail-on-error behaviour if configured

Usage:
    python pipeline.py [--config config.ini]

Exit codes:
    0 = full pipeline succeeded
    1 = config problem
    2 = a pipeline step failed
    3 = another instance is already running (lock held)
"""

import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

from common import load_config, setup_logging, get_bool, send_smtp_message

LOCK_FILENAME = "pipeline.lock"
LOCK_MAX_AGE_SECONDS = 60 * 60 * 2  # 2 hours — treat older locks as stale (crashed run)
EMAIL_STEP = "03_send_email.py"     # if this one fails, emailing about it would fail too


def acquire_lock(lock_path, logger):
    if os.path.exists(lock_path):
        age = time.time() - os.path.getmtime(lock_path)
        if age < LOCK_MAX_AGE_SECONDS:
            logger.error(
                f"Lock file present ({lock_path}), age {age:.0f}s. "
                "Another run may be in progress. Exiting."
            )
            sys.exit(3)
        else:
            logger.warning(f"Stale lock file (age {age:.0f}s) found — removing and continuing.")
            os.remove(lock_path)

    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))


def release_lock(lock_path):
    if os.path.exists(lock_path):
        os.remove(lock_path)


def extract_errors(output):
    """
    Pull the human-readable ERROR messages out of a step's output.
    Log lines look like: '2026-09-20 07:00:01 | ERROR   | download | message'.
    Falls back to the last lines of output (e.g. a Python traceback).
    """
    errors = []
    for line in output.splitlines():
        if "| ERROR" in line:
            errors.append(line.split("|", 3)[-1].strip())
    if errors:
        return errors
    tail = [ln for ln in output.splitlines() if ln.strip()][-15:]
    return tail or ["(no output captured — see logs/sipca_pipeline.log)"]


def run_step(script_name, config_path, logger):
    """Run one step. Returns (ok, returncode, error_lines)."""
    logger.info(f"--- Running {script_name} ---")
    result = subprocess.run(
        [sys.executable, script_name, "--config", config_path],
        cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = result.stdout or ""
    if output:
        print(output, end="", flush=True)  # keep step output visible in cron.log / terminal

    if result.returncode != 0:
        logger.error(f"{script_name} failed with exit code {result.returncode}")
        return False, result.returncode, extract_errors(output)

    logger.info(f"--- {script_name} completed successfully ---")
    return True, 0, []


def send_failure_alert(cfg, step, returncode, error_lines, log_dir, logger):
    """Email an explanation of the failure. Never raises — alerting must not mask the real error."""
    try:
        if not get_bool(cfg, "smtp", "enabled", default=False):
            logger.info("Failure alert not sent: smtp.enabled is false.")
            return
        if not get_bool(cfg, "smtp", "send_on_failure", default=True):
            logger.info("Failure alert not sent: smtp.send_on_failure is false.")
            return

        smtp_cfg = cfg["smtp"]
        stale = any(line.startswith("STALE DATA") for line in error_lines)
        if stale:
            subject = smtp_cfg.get(
                "subject_stale", "SIPCA Pipeline STOPPED : Source data in Azure is stale"
            )
        else:
            subject = smtp_cfg.get("subject_failure", "SIPCA Pipeline FAILED") + f" : {step}"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        body = "\n".join(
            [
                "The SIPCA pipeline did not complete, so no review report was produced.",
                "",
                f"Time        : {now}",
                f"Server      : {socket.gethostname()}",
                f"Failed step : {step} (exit code {returncode})",
                "",
                "What went wrong:",
                *[f"  - {line}" for line in error_lines],
                "",
                f"Full details: {os.path.abspath(os.path.join(log_dir, 'sipca_pipeline.log'))}",
            ]
        )

        to_addrs = send_smtp_message(cfg, subject, body)
        logger.info(f"[✓] Failure alert sent to: {', '.join(to_addrs)}")
    except Exception as e:
        logger.error(f"Could not send failure alert email: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run the full SIPCA pipeline")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__)) or "."
    config_path = args.config

    cfg = load_config(config_path)
    log_dir = cfg["paths"].get("log_dir", "./logs")
    logger = setup_logging(log_dir, "pipeline")

    lock_path = os.path.join(base_dir, LOCK_FILENAME)
    acquire_lock(lock_path, logger)

    try:
        logger.info("=" * 60)
        logger.info("SIPCA pipeline run starting")
        logger.info("=" * 60)

        steps = ["01_download_event_index.py", "02_sipca_review.py", EMAIL_STEP]
        for step in steps:
            ok, returncode, error_lines = run_step(step, config_path, logger)
            if not ok:
                logger.error("Pipeline aborted due to step failure.")
                if step != EMAIL_STEP:
                    send_failure_alert(cfg, step, returncode, error_lines, log_dir, logger)
                sys.exit(2)

        logger.info("Pipeline run completed successfully.")

    finally:
        release_lock(lock_path)


if __name__ == "__main__":
    main()

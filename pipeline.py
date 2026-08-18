"""
Runs the full SIPCA weekly review pipeline in order:
  1. download_event_index.py  — pull latest event_index.json from Azure
  2. sipca_review.py          — classify records, build report, write status
  3. send_email.py            — notify recipients if action is required

Designed to be invoked by cron. Includes:
  - A lock file so overlapping cron runs don't collide if one run is slow
  - Clear, timestamped logging to logs/sipca_pipeline.log
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
import subprocess
import sys
import time

from common import load_config, setup_logging

LOCK_FILENAME = "pipeline.lock"
LOCK_MAX_AGE_SECONDS = 60 * 60 * 2  # 2 hours — treat older locks as stale (crashed run)


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


def run_step(script_name, config_path, logger):
    logger.info(f"--- Running {script_name} ---")
    result = subprocess.run(
        [sys.executable, script_name, "--config", config_path],
        cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
    )
    if result.returncode != 0:
        logger.error(f"{script_name} failed with exit code {result.returncode}")
        return False
    logger.info(f"--- {script_name} completed successfully ---")
    return True


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

        steps = ["01_download_event_index.py", "02_sipca_review.py", "03_send_email.py"]
        for step in steps:
            ok = run_step(step, config_path, logger)
            if not ok:
                logger.error("Pipeline aborted due to step failure.")
                sys.exit(2)

        logger.info("Pipeline run completed successfully.")

    finally:
        release_lock(lock_path)


if __name__ == "__main__":
    main()

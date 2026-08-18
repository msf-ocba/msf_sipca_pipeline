"""
Shared helpers for the SIPCA pipeline: config loading and logging setup.
Keeping this in one place means download / review / email / pipeline
scripts all behave consistently and log to the same place.
"""

import configparser
import logging
import logging.handlers
import os
import sys


def load_config(config_path: str = "config.ini") -> configparser.ConfigParser:
    """Load config.ini and fail loudly (with a clear message) if it's missing."""
    if not os.path.exists(config_path):
        example = os.path.join(os.path.dirname(os.path.abspath(config_path)) or ".", "config.example.ini")
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Copy config.example.ini to {config_path} and fill in your real values.\n"
            f"(Example file expected at: {example})"
        )
    parser = configparser.ConfigParser()
    parser.read(config_path)
    return parser


def setup_logging(log_dir: str, logger_name: str) -> logging.Logger:
    """
    Configure a logger that writes to both stdout (for cron mail / manual runs)
    and a rotating log file (so logs don't grow unbounded over months of cron runs).
    """
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "sipca_pipeline.log")

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        # Already configured (e.g. re-imported within same process) — don't duplicate handlers
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    return logger


def get_bool(cfg: configparser.ConfigParser, section: str, key: str, default: bool = False) -> bool:
    try:
        return cfg.getboolean(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return default

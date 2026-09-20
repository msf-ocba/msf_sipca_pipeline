"""
Shared helpers for the SIPCA pipeline: config loading, logging setup and
SMTP sending. Keeping this in one place means download / review / email /
pipeline scripts all behave consistently and log to the same place.
"""

import configparser
import logging
import logging.handlers
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def load_config(config_path: str = "config.ini") -> configparser.ConfigParser:
    """Load config.ini and fail loudly (with a clear message) if it's missing."""
    if not os.path.exists(config_path):
        example = os.path.join(os.path.dirname(os.path.abspath(config_path)) or ".", "config.example.ini")
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Copy config.example.ini to {config_path} and fill in your real values.\n"
            f"(Example file expected at: {example})"
        )
    # interpolation=None: SAS tokens and passwords often contain '%' characters
    # (e.g. %3D), which the default parser would try to treat as variable
    # references and fail with "invalid interpolation syntax".
    parser = configparser.ConfigParser(interpolation=None)
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


def send_smtp_message(cfg: configparser.ConfigParser, subject: str, body: str):
    """
    Send a plain-text email using the [smtp] section of config.ini.

    Raises ValueError if the SMTP settings are incomplete; any other
    exception (network, auth, ...) propagates so the caller can decide how
    to handle it. Returns the list of recipients on success.
    """
    smtp_cfg = cfg["smtp"]

    host = smtp_cfg.get("host", "").strip()
    port = smtp_cfg.getint("port", fallback=587)
    user = smtp_cfg.get("user", "").strip()
    password = smtp_cfg.get("password", "").strip()
    from_addr = smtp_cfg.get("from_addr", "").strip()
    to_addrs = [addr.strip() for addr in smtp_cfg.get("to_addrs", "").split(",") if addr.strip()]

    if not (host and user and password and from_addr and to_addrs):
        raise ValueError(
            "SMTP config incomplete. Check host, user, password, from_addr, "
            "and to_addrs in config.ini under [smtp]."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(from_addr, to_addrs, msg.as_string())

    return to_addrs

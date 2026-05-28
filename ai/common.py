"""Shared helpers for the TNBIKE AI layer."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
AI_ROOT = Path(__file__).resolve().parent
TNBIKE_PROJECT_ROOT = (
    WORKSPACE_ROOT
    if (WORKSPACE_ROOT / "src").is_dir()
    else WORKSPACE_ROOT / "tnbike-project"
)
AI_ENV_FILE = AI_ROOT / ".env"
TNBIKE_ENV_FILE = TNBIKE_PROJECT_ROOT / ".env"
LOG_DIR = AI_ROOT / "logs"
PENDING_ISSUES_LOG = LOG_DIR / "pending_issues.log"


def ensure_runtime_paths() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_environment() -> None:
    """Load AI env first, then keep existing values while loading project env."""
    load_dotenv(AI_ENV_FILE, override=False)
    load_dotenv(TNBIKE_ENV_FILE, override=False)


def ensure_project_on_path() -> None:
    project_path = str(TNBIKE_PROJECT_ROOT)
    if project_path not in sys.path:
        sys.path.insert(0, project_path)


def setup_logging(name: str = "ai") -> logging.Logger:
    ensure_runtime_paths()
    level_name = os.getenv("AI_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

        file_handler = logging.FileHandler(LOG_DIR / "ai_pipeline.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    return logger


def log_pending_issue(message: str) -> None:
    ensure_runtime_paths()
    with PENDING_ISSUES_LOG.open("a", encoding="utf-8") as f:
        f.write(message.rstrip() + "\n")

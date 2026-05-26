# ============================================================
# src/config/settings.py
# Central project settings for TNBIKE project
# ============================================================

import os
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv


# ============================================================
# PROJECT ROOT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)


# ============================================================
# ENV HELPERS
# ============================================================

def get_env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Thiếu biến môi trường: {name}. Hãy kiểm tra file .env ở root project."
        )

    return value


def get_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "y", "t"}


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    return int(value)


# ============================================================
# PATH HELPERS
# ============================================================

def resolve_project_path(path: str | Path) -> Path:
    path = Path(path)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def ensure_dir(path: str | Path) -> Path:
    path = resolve_project_path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ============================================================
# DATA PATHS
# ============================================================

DATA_DIR = resolve_project_path("data")

BACKUP_DIR = resolve_project_path("data/backup")

INCOMING_DIR = resolve_project_path("data/incoming")
INCOMING_EML_DIR = resolve_project_path("data/incoming/eml")

PROCESSED_DIR = resolve_project_path("data/processed")
STAGING_DIR = resolve_project_path("data/processed/staging")
QUALITY_CHECK_DIR = resolve_project_path("data/processed/quality_check")
CLEANED_DIR = resolve_project_path("data/processed/cleaned")
MAPPING_DIR = resolve_project_path("data/processed/mapping")

PROCESSED_PIPELINE_DIR = resolve_project_path("data/processed")
PROCESSED_SUCCESS_EML_DIR = resolve_project_path("data/processed/success_eml/eml")
PROCESSED_FAILED_EML_DIR = resolve_project_path("data/processed/failed_eml/eml")

LOG_DIR = resolve_project_path("logs")

SQL_DIR = resolve_project_path("sql")

SCHEDULES_DIR = resolve_project_path("schedules")
PIPELINE_SCHEDULE_CONFIG = resolve_project_path("schedules/pipeline_schedule.yaml")


# ============================================================
# PIPELINE DEFAULT PATHS (for CLI defaults)
# ============================================================

DEFAULT_INPUT_DIR = INCOMING_EML_DIR
DEFAULT_STAGING_DIR = STAGING_DIR
DEFAULT_QUALITY_CHECK_DIR = QUALITY_CHECK_DIR
DEFAULT_SUCCESS_DIR = PROCESSED_SUCCESS_EML_DIR
DEFAULT_FAILED_DIR = PROCESSED_FAILED_EML_DIR


# ============================================================
# DATABASE SETTINGS
# ============================================================

@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: str
    database: str
    user: str
    password: str
    schema: str


def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings(
        host=get_required_env("PGHOST"),
        port=get_env("PGPORT", "5432"),
        database=get_required_env("PGDATABASE"),
        user=get_required_env("PGUSER"),
        password=get_required_env("PGPASSWORD"),
        schema=get_env("DB_SCHEMA", "tnbike"),
    )


# ============================================================
# PIPELINE DEFAULT SETTINGS
# ============================================================

@dataclass(frozen=True)
class PipelinePathSettings:
    input_dir: Path
    staging_dir: Path
    quality_check_dir: Path
    success_dir: Path
    failed_dir: Path
    backup_dir: Path
    log_dir: Path


def get_pipeline_path_settings() -> PipelinePathSettings:
    return PipelinePathSettings(
        input_dir=INCOMING_EML_DIR,
        staging_dir=STAGING_DIR,
        quality_check_dir=QUALITY_CHECK_DIR,
        success_dir=PROCESSED_SUCCESS_EML_DIR,
        failed_dir=PROCESSED_FAILED_EML_DIR,
        backup_dir=BACKUP_DIR,
        log_dir=LOG_DIR,
    )


def ensure_project_dirs() -> None:
    """
    Tạo các folder cần thiết cho pipeline.
    """

    dirs = [
        DATA_DIR,
        BACKUP_DIR,
        INCOMING_DIR,
        INCOMING_EML_DIR,
        PROCESSED_DIR,
        STAGING_DIR,
        QUALITY_CHECK_DIR,
        CLEANED_DIR,
        MAPPING_DIR,
        PROCESSED_PIPELINE_DIR,
        PROCESSED_SUCCESS_EML_DIR,
        PROCESSED_FAILED_EML_DIR,
        LOG_DIR,
        SCHEDULES_DIR,
    ]

    for path in dirs:
        path.mkdir(parents=True, exist_ok=True)


# ============================================================
# DEBUG
# ============================================================

def print_settings() -> None:
    db = get_database_settings()
    paths = get_pipeline_path_settings()

    print("PROJECT_ROOT:", PROJECT_ROOT)
    print("DB HOST     :", db.host)
    print("DB PORT     :", db.port)
    print("DB NAME     :", db.database)
    print("DB USER     :", db.user)
    print("DB SCHEMA   :", db.schema)
    print("INPUT DIR   :", paths.input_dir)
    print("STAGING DIR :", paths.staging_dir)
    print("QUALITY DIR :", paths.quality_check_dir)
    print("SUCCESS DIR :", paths.success_dir)
    print("FAILED DIR  :", paths.failed_dir)


if __name__ == "__main__":
    ensure_project_dirs()
    print_settings()
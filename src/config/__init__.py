# ============================================================
# src/config/__init__.py
# Public API for configuration modules
# ============================================================

from src.config.settings import (
    PROJECT_ROOT,
    ENV_FILE,
    get_env,
    get_required_env,
    get_env_bool,
    get_env_int,
    resolve_project_path,
    ensure_dir,
    ensure_project_dirs,
    # Paths
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
    SQL_DIR,
    SCHEDULES_DIR,
    PIPELINE_SCHEDULE_CONFIG,
    DEFAULT_INPUT_DIR,
    DEFAULT_STAGING_DIR,
    DEFAULT_QUALITY_CHECK_DIR,
    DEFAULT_SUCCESS_DIR,
    DEFAULT_FAILED_DIR,
    # Database
    DatabaseSettings,
    get_database_settings,
    PipelinePathSettings,
    get_pipeline_path_settings,
    print_settings,
)
from src.config.logging_config import setup_logging, get_logger

__all__ = [
    # settings
    "PROJECT_ROOT",
    "ENV_FILE",
    "get_env",
    "get_required_env",
    "get_env_bool",
    "get_env_int",
    "resolve_project_path",
    "ensure_dir",
    "ensure_project_dirs",
    # Paths
    "DATA_DIR",
    "BACKUP_DIR",
    "INCOMING_DIR",
    "INCOMING_EML_DIR",
    "PROCESSED_DIR",
    "STAGING_DIR",
    "QUALITY_CHECK_DIR",
    "CLEANED_DIR",
    "MAPPING_DIR",
    "PROCESSED_PIPELINE_DIR",
    "PROCESSED_SUCCESS_EML_DIR",
    "PROCESSED_FAILED_EML_DIR",
    "LOG_DIR",
    "SQL_DIR",
    "SCHEDULES_DIR",
    "PIPELINE_SCHEDULE_CONFIG",
    "DEFAULT_INPUT_DIR",
    "DEFAULT_STAGING_DIR",
    "DEFAULT_QUALITY_CHECK_DIR",
    "DEFAULT_SUCCESS_DIR",
    "DEFAULT_FAILED_DIR",
    # Database
    "DatabaseSettings",
    "get_database_settings",
    "PipelinePathSettings",
    "get_pipeline_path_settings",
    "print_settings",
    # logging
    "setup_logging",
    "get_logger",
]

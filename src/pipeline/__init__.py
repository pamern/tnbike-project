# ============================================================
# src/pipeline/__init__.py
# Public API for pipeline modules
# ============================================================

from src.pipeline.run_pipeline import run_pipeline
from src.pipeline.extract_to_staging import extract_emails_to_staging
from src.pipeline.load_staging_to_db import load_staging_to_db
from src.pipeline.update_fact_sales import update_fact_sales
from src.pipeline.move_processed_file import move_processed_files
from src.pipeline.fallback import (
    create_pipeline_restore_point,
    create_timestamped_pipeline_restore_point,
    restore_pipeline_restore_point,
    rollback_pipeline_test,
)

__all__ = [
    "run_pipeline",
    "extract_emails_to_staging",
    "load_staging_to_db",
    "update_fact_sales",
    "move_processed_files",
    "create_pipeline_restore_point",
    "create_timestamped_pipeline_restore_point",
    "restore_pipeline_restore_point",
    "rollback_pipeline_test",
]

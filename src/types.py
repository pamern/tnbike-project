# ============================================================
# src/types.py
# Type definitions for TNBIKE Pipeline
# ============================================================

from typing import Any, Optional, TypedDict


class StepResult(TypedDict, total=False):
    """Result from a pipeline step execution."""
    step: str
    status: str  # SUCCESS, FAILED, DRY_RUN, SKIPPED
    elapsed: str
    returncode: Optional[int]
    error: Optional[str]
    result: Any
    reason: Optional[str]


class ExtractResult(TypedDict, total=False):
    """Result from email extraction step."""
    total_emails: int
    processed_emails: int
    failed_emails: int
    quality_check_dir: str
    output_paths: list[str]
    output_dir: str


class LoadDBResult(TypedDict, total=False):
    """Result from database load step."""
    total_records: int
    inserted_records: int
    updated_records: int
    failed_records: int
    duration: str


class UpdateFactSalesResult(TypedDict, total=False):
    """Result from fact sales update step."""
    total_records: int
    deleted_records: int
    inserted_records: int
    so_numbers_count: int


class MoveFilesResult(TypedDict, total=False):
    """Result from moving processed files step."""
    total_files: int
    success_files: int
    failed_files: int
    success_dir: str
    failed_dir: str


class PipelineSummary(TypedDict, total=False):
    """Overall pipeline execution summary."""
    started_at: str
    restore_point: Optional[str]
    extract: Optional[ExtractResult]
    load_db: Optional[LoadDBResult]
    update_fact_sales: Optional[UpdateFactSalesResult]
    move_files: Optional[MoveFilesResult]
    dry_run: bool
    success: bool
    error: str
    elapsed: str


class PreprocessingStepResult(TypedDict, total=False):
    """Result from a preprocessing step."""
    step: str
    command: str
    status: str
    returncode: Optional[int]
    elapsed: str
    reason: Optional[str]


class PreprocessingSummary(TypedDict, total=False):
    """Overall preprocessing execution summary."""
    started_at: str
    restore_point: Optional[str]
    dry_run: bool
    success: bool
    steps: list[PreprocessingStepResult]
    error: str
    elapsed: str

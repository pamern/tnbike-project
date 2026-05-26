# ============================================================
# src/utils/__init__.py
# Public API for utility modules
# ============================================================

from src.utils.time_utils import now_text, format_seconds
from src.utils.file_utils import (
    get_project_root,
    resolve_project_path,
    ensure_dir,
    ensure_parent_dir,
    list_files,
    list_eml_files,
    list_pdf_files,
    file_exists,
    folder_exists,
    safe_filename,
    timestamp_str,
    add_timestamp_to_filename,
    make_unique_path,
    copy_file,
    move_file,
    move_to_folder,
    copy_to_folder,
    delete_file,
    get_file_hash,
    get_file_size,
    read_json,
    write_json,
    setup_pipeline_folders,
    normalize_so_number,
    extract_so_number_from_filename,
)
from src.utils.executor import PipelineStepExecutor

__all__ = [
    # time_utils
    "now_text",
    "format_seconds",
    # file_utils
    "get_project_root",
    "resolve_project_path",
    "ensure_dir",
    "ensure_parent_dir",
    "list_files",
    "list_eml_files",
    "list_pdf_files",
    "file_exists",
    "folder_exists",
    "safe_filename",
    "timestamp_str",
    "add_timestamp_to_filename",
    "make_unique_path",
    "copy_file",
    "move_file",
    "move_to_folder",
    "copy_to_folder",
    "delete_file",
    "get_file_hash",
    "get_file_size",
    "read_json",
    "write_json",
    "setup_pipeline_folders",
    "normalize_so_number",
    "extract_so_number_from_filename",
    # executor
    "PipelineStepExecutor",
]

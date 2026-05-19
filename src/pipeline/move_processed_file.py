# ============================================================
# src/pipeline/move_processed_file.py
# Move processed .eml files after extract/load pipeline
#
# Input:
#   data/incoming/eml/*.eml
#   data/processed/staging/staging_email_log.csv
#   data/processed/quality_check/extract_fail.csv
#
# Output:
#   data/processed/success_eml/eml/*.eml
#   data/processed/failed_eml/eml/*.eml
#
# Rule:
#   SUCCESS / NEEDS_REVIEW -> success/eml
#   FAILED                 -> failed/eml
#
# Fallback:
#   Nếu không map được bằng message_id thì đọc extract_fail.csv:
#       - fatal error -> failed
#       - chỉ lỗi order_line -> success, vì vẫn có dữ liệu hợp lệ cần load/review
# ============================================================

import csv
import re
import sys
import shutil
import argparse
from pathlib import Path
from email import policy
from email.parser import BytesParser
from collections import Counter

# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger


logger = get_logger(__name__)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_INPUT_DIR = "data/incoming/eml"

DEFAULT_EMAIL_LOG_FILE = "data/processed/staging/staging_email_log.csv"

# Chuẩn mới
DEFAULT_EXTRACT_FAIL_FILE = "data/processed/quality_check/extract_fail.csv"

# Fallback nếu còn dùng cấu trúc cũ
LEGACY_EXTRACT_FAIL_FILE = "data/processed/staging/staging_fail.csv"

DEFAULT_SUCCESS_DIR = "data/processed/success_eml/eml"
DEFAULT_FAILED_DIR = "data/processed/failed_eml/eml"

SUCCESS_STATUSES = {"SUCCESS", "NEEDS_REVIEW"}
FAILED_STATUSES = {"FAILED"}

FATAL_FAIL_RECORD_TYPES = {
    "file",
    "sales_order",
    "order_line_blocked_by_sales_order_error",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value: str | None) -> str:
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def read_csv_dicts(path: str | Path) -> list[dict]:
    """
    Đọc CSV UTF-8/UTF-8-SIG thành list[dict].
    Nếu file không tồn tại thì trả về [].
    """

    path = resolve_project_path(path)

    if not path.exists():
        logger.warning("CSV file not found, skipped: %s", path)
        return []

    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def parse_message_id_from_eml(eml_path: str | Path) -> str:
    """
    Đọc nhanh Message-ID từ file .eml.
    Không extract body/PDF để move cho nhanh.
    """

    eml_path = Path(eml_path)

    try:
        with open(eml_path, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)

        return clean_text(msg.get("Message-ID", ""))

    except Exception as e:
        logger.warning("Cannot parse message_id from %s: %s", eml_path, e)
        return ""


def build_unique_target_path(target_path: Path) -> Path:
    """
    Nếu file đích đã tồn tại thì tạo tên mới để không ghi đè.

    Example:
        BH26_0935.eml
        BH26_0935__move_001.eml
    """

    if not target_path.exists():
        return target_path

    parent = target_path.parent
    stem = target_path.stem
    suffix = target_path.suffix

    counter = 1

    while True:
        candidate = parent / f"{stem}__move_{counter:03d}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def move_or_copy_file(
    source_path: Path,
    target_dir: Path,
    overwrite: bool = False,
    copy_only: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Move/copy 1 file sang target_dir.
    """

    target_dir = ensure_dir(target_dir)
    target_path = target_dir / source_path.name

    if target_path.exists() and not overwrite:
        target_path = build_unique_target_path(target_path)

    result = {
        "source_file": str(source_path),
        "target_file": str(target_path),
        "action": "COPY" if copy_only else "MOVE",
        "status": "DRY_RUN" if dry_run else "DONE",
        "error": "",
    }

    if dry_run:
        logger.info(
            "[DRY RUN] %s: %s -> %s",
            "Copy" if copy_only else "Move",
            source_path,
            target_path,
        )
        return result

    try:
        if overwrite and target_path.exists():
            target_path.unlink()

        if copy_only:
            shutil.copy2(str(source_path), str(target_path))
        else:
            shutil.move(str(source_path), str(target_path))

        logger.info(
            "%s file: %s -> %s",
            "Copied" if copy_only else "Moved",
            source_path,
            target_path,
        )

    except Exception as e:
        logger.exception("Failed to move/copy file: %s", source_path)

        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


# ============================================================
# LOOKUP BUILDERS
# ============================================================

def build_email_status_lookup(email_log_file: str | Path) -> dict[str, dict]:
    """
    Build lookup:
        message_id -> {
            processing_status,
            processing_reason,
            attachment_name
        }

    Nếu staging_email_log có nhiều dòng trùng message_id thì dòng sau ghi đè dòng trước,
    đúng với logic upsert theo message_id.
    """

    rows = read_csv_dicts(email_log_file)
    lookup = {}

    for row in rows:
        message_id = clean_text(row.get("message_id", ""))

        if not message_id:
            continue

        lookup[message_id] = {
            "processing_status": clean_text(row.get("processing_status", "")).upper(),
            "processing_reason": clean_text(row.get("processing_reason", "")).upper(),
            "attachment_name": clean_text(row.get("attachment_name", "")),
        }

    logger.info("Email status lookup loaded: %s message_id", len(lookup))

    return lookup


def resolve_extract_fail_file(
    extract_fail_file: str | Path | None = None,
) -> Path | None:
    """
    Ưu tiên file fail theo cấu trúc mới.
    Nếu không có thì fallback sang staging_fail.csv cũ.
    """

    if extract_fail_file:
        path = resolve_project_path(extract_fail_file)

        if path.exists():
            return path

        logger.warning("Given extract fail file does not exist: %s", path)
        return None

    new_path = resolve_project_path(DEFAULT_EXTRACT_FAIL_FILE)

    if new_path.exists():
        return new_path

    legacy_path = resolve_project_path(LEGACY_EXTRACT_FAIL_FILE)

    if legacy_path.exists():
        logger.warning("Using legacy fail file: %s", legacy_path)
        return legacy_path

    return None


def build_fail_file_lookup(extract_fail_file: str | Path | None = None) -> dict[str, dict]:
    """
    Build lookup theo source_email_file từ extract_fail.csv.

    Result:
        source_email_file -> {
            has_fail: bool,
            has_fatal_fail: bool,
            record_types: set,
            error_count: int
        }
    """

    fail_path = resolve_extract_fail_file(extract_fail_file)

    if fail_path is None:
        logger.warning("No extract fail file found")
        return {}

    rows = read_csv_dicts(fail_path)
    lookup = {}

    for row in rows:
        source_email_file = clean_text(row.get("source_email_file", ""))

        if not source_email_file:
            continue

        record_type = clean_text(row.get("record_type", ""))

        item = lookup.setdefault(
            source_email_file,
            {
                "has_fail": False,
                "has_fatal_fail": False,
                "record_types": set(),
                "error_count": 0,
            },
        )

        item["has_fail"] = True
        item["record_types"].add(record_type)
        item["error_count"] += 1

        if record_type in FATAL_FAIL_RECORD_TYPES:
            item["has_fatal_fail"] = True

    logger.info("Fail file lookup loaded: %s source files", len(lookup))

    return lookup


# ============================================================
# DECISION LOGIC
# ============================================================

def decide_file_destination(
    eml_path: Path,
    email_status_lookup: dict[str, dict],
    fail_file_lookup: dict[str, dict],
) -> dict:
    """
    Quyết định file đi success hay failed.

    Priority:
        1. Match bằng message_id trong staging_email_log
        2. Fallback bằng source_email_file trong extract_fail.csv
        3. Không đủ thông tin -> SKIPPED
    """

    source_email_file = eml_path.name
    message_id = parse_message_id_from_eml(eml_path)

    # --------------------------------------------------------
    # Priority 1: message_id từ staging_email_log
    # --------------------------------------------------------
    if message_id and message_id in email_status_lookup:
        log_info = email_status_lookup[message_id]
        status = log_info["processing_status"]
        reason = log_info["processing_reason"]

        if status in SUCCESS_STATUSES:
            return {
                "decision": "SUCCESS",
                "source": "email_log",
                "message_id": message_id,
                "processing_status": status,
                "processing_reason": reason,
                "note": "",
            }

        if status in FAILED_STATUSES:
            return {
                "decision": "FAILED",
                "source": "email_log",
                "message_id": message_id,
                "processing_status": status,
                "processing_reason": reason,
                "note": "",
            }

        return {
            "decision": "SKIPPED",
            "source": "email_log",
            "message_id": message_id,
            "processing_status": status,
            "processing_reason": reason,
            "note": f"Unknown processing_status: {status}",
        }

    # --------------------------------------------------------
    # Priority 2: fallback bằng extract_fail.csv
    # --------------------------------------------------------
    fail_info = fail_file_lookup.get(source_email_file)

    if fail_info:
        if fail_info["has_fatal_fail"]:
            return {
                "decision": "FAILED",
                "source": "extract_fail",
                "message_id": message_id,
                "processing_status": "FAILED",
                "processing_reason": "FATAL_FAIL",
                "note": f"record_types={sorted(fail_info['record_types'])}",
            }

        return {
            "decision": "SUCCESS",
            "source": "extract_fail",
            "message_id": message_id,
            "processing_status": "NEEDS_REVIEW",
            "processing_reason": "NON_FATAL_FAIL",
            "note": f"record_types={sorted(fail_info['record_types'])}",
        }

    # --------------------------------------------------------
    # Priority 3: không đủ thông tin
    # --------------------------------------------------------
    return {
        "decision": "SKIPPED",
        "source": "none",
        "message_id": message_id,
        "processing_status": "",
        "processing_reason": "",
        "note": "No status found in email_log or extract_fail",
    }


# ============================================================
# MAIN PROCESS
# ============================================================

def move_processed_files(
    input_dir: str | Path = DEFAULT_INPUT_DIR,
    success_dir: str | Path = DEFAULT_SUCCESS_DIR,
    failed_dir: str | Path = DEFAULT_FAILED_DIR,
    email_log_file: str | Path = DEFAULT_EMAIL_LOG_FILE,
    extract_fail_file: str | Path | None = None,
    overwrite: bool = False,
    copy_only: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Move .eml đã xử lý sang processed_pipeline.

    SUCCESS / NEEDS_REVIEW -> success_dir
    FAILED                 -> failed_dir
    SKIPPED                -> giữ nguyên tại incoming
    """

    input_dir = resolve_project_path(input_dir)
    success_dir = ensure_dir(success_dir)
    failed_dir = ensure_dir(failed_dir)

    logger.info("=" * 80)
    logger.info("MOVE PROCESSED FILES STARTED")
    logger.info("=" * 80)
    logger.info("Input dir       : %s", input_dir)
    logger.info("Success dir     : %s", success_dir)
    logger.info("Failed dir      : %s", failed_dir)
    logger.info("Email log file  : %s", resolve_project_path(email_log_file))
    logger.info("Overwrite       : %s", overwrite)
    logger.info("Copy only       : %s", copy_only)
    logger.info("Dry run         : %s", dry_run)
    logger.info("=" * 80)

    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    email_status_lookup = build_email_status_lookup(email_log_file)
    fail_file_lookup = build_fail_file_lookup(extract_fail_file)

    eml_files = sorted(input_dir.glob("*.eml"))

    logger.info("Found incoming .eml files: %s", len(eml_files))

    results = []

    for eml_path in eml_files:
        decision_info = decide_file_destination(
            eml_path=eml_path,
            email_status_lookup=email_status_lookup,
            fail_file_lookup=fail_file_lookup,
        )

        decision = decision_info["decision"]

        if decision == "SUCCESS":
            target_dir = success_dir
        elif decision == "FAILED":
            target_dir = failed_dir
        else:
            result = {
                "source_file": str(eml_path),
                "target_file": "",
                "decision": "SKIPPED",
                "decision_source": decision_info["source"],
                "processing_status": decision_info["processing_status"],
                "processing_reason": decision_info["processing_reason"],
                "message_id": decision_info["message_id"],
                "note": decision_info["note"],
                "move_status": "SKIPPED",
                "error": "",
            }

            logger.info(
                "Skipped file: %s | reason=%s",
                eml_path.name,
                decision_info["note"],
            )

            results.append(result)
            continue

        move_result = move_or_copy_file(
            source_path=eml_path,
            target_dir=target_dir,
            overwrite=overwrite,
            copy_only=copy_only,
            dry_run=dry_run,
        )

        results.append(
            {
                "source_file": str(eml_path),
                "target_file": move_result["target_file"],
                "decision": decision,
                "decision_source": decision_info["source"],
                "processing_status": decision_info["processing_status"],
                "processing_reason": decision_info["processing_reason"],
                "message_id": decision_info["message_id"],
                "note": decision_info["note"],
                "move_status": move_result["status"],
                "error": move_result["error"],
            }
        )

    decision_counts = Counter(row["decision"] for row in results)
    move_status_counts = Counter(row["move_status"] for row in results)

    summary = {
        "input_files": len(eml_files),
        "moved_success": decision_counts.get("SUCCESS", 0),
        "moved_failed": decision_counts.get("FAILED", 0),
        "skipped": decision_counts.get("SKIPPED", 0),
        "decision_counts": dict(decision_counts),
        "move_status_counts": dict(move_status_counts),
        "results": results,
    }

    logger.info("=" * 80)
    logger.info("MOVE PROCESSED FILES FINISHED")
    logger.info("Input files        : %s", summary["input_files"])
    logger.info("Moved to success   : %s", summary["moved_success"])
    logger.info("Moved to failed    : %s", summary["moved_failed"])
    logger.info("Skipped            : %s", summary["skipped"])
    logger.info("Decision counts    : %s", summary["decision_counts"])
    logger.info("Move status counts : %s", summary["move_status_counts"])
    logger.info("=" * 80)

    return summary


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move processed .eml files to success/failed folders"
    )

    parser.add_argument(
        "--input-dir",
        default=DEFAULT_INPUT_DIR,
        help="Input folder containing .eml files",
    )

    parser.add_argument(
        "--success-dir",
        default=DEFAULT_SUCCESS_DIR,
        help="Folder for successfully processed .eml files",
    )

    parser.add_argument(
        "--failed-dir",
        default=DEFAULT_FAILED_DIR,
        help="Folder for failed .eml files",
    )

    parser.add_argument(
        "--email-log-file",
        default=DEFAULT_EMAIL_LOG_FILE,
        help="staging_email_log.csv path",
    )

    parser.add_argument(
        "--extract-fail-file",
        default=None,
        help="extract_fail.csv path. If omitted, auto-detect quality_check or legacy staging_fail",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files in success/failed folders",
    )

    parser.add_argument(
        "--copy-only",
        action="store_true",
        help="Copy files instead of moving. Useful for testing",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without moving/copying files",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="move_processed_file.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        summary = move_processed_files(
            input_dir=args.input_dir,
            success_dir=args.success_dir,
            failed_dir=args.failed_dir,
            email_log_file=args.email_log_file,
            extract_fail_file=args.extract_fail_file,
            overwrite=args.overwrite,
            copy_only=args.copy_only,
            dry_run=args.dry_run,
        )

        print("")
        print("MOVE PROCESSED FILES SUCCESS")
        print(f"Input files        : {summary['input_files']}")
        print(f"Moved to success   : {summary['moved_success']}")
        print(f"Moved to failed    : {summary['moved_failed']}")
        print(f"Skipped            : {summary['skipped']}")
        print(f"Decision counts    : {summary['decision_counts']}")
        print(f"Move status counts : {summary['move_status_counts']}")

    except Exception as e:
        logger.exception("MOVE PROCESSED FILES FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
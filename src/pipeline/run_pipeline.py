# ============================================================
# src/pipeline/run_pipeline.py
# TNBIKE Pipeline Orchestrator
#
# Flow:
#   1. Create DB restore point
#   2. Extract emails -> staging CSV
#   3. Load staging CSV -> DB
#   4. Update fact_sales
#   5. Move processed .eml files
#
# Notes:
#   - Restore point được tạo trước khi chạy để tiện fallback.
#   - move_processed_file chỉ chạy sau khi DB + fact_sales thành công.
#   - Nếu chạy --limit để test thì mặc định KHÔNG move file.
# ============================================================

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.config.logging_config import setup_logging, get_logger
    from src.config.settings import (
        DEFAULT_INPUT_DIR,
        DEFAULT_STAGING_DIR,
        DEFAULT_QUALITY_CHECK_DIR,
        DEFAULT_SUCCESS_DIR,
        DEFAULT_FAILED_DIR,
    )
    from src.utils.file_utils import resolve_project_path
    from src.utils.time_utils import now_text, format_seconds

    from src.pipeline.fallback import (
        create_pipeline_restore_point,
        create_timestamped_pipeline_restore_point,
        restore_pipeline_restore_point,
        rollback_pipeline_test,
    )

    from src.pipeline.extract_to_staging import extract_emails_to_staging
    from src.pipeline.load_staging_to_db import load_staging_to_db
    from src.pipeline.update_fact_sales import update_fact_sales
    from src.pipeline.move_processed_file import move_processed_files

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.config.logging_config import setup_logging, get_logger
    from src.config.settings import (
        DEFAULT_INPUT_DIR,
        DEFAULT_STAGING_DIR,
        DEFAULT_QUALITY_CHECK_DIR,
        DEFAULT_SUCCESS_DIR,
        DEFAULT_FAILED_DIR,
    )
    from src.utils.file_utils import resolve_project_path
    from src.utils.time_utils import now_text, format_seconds

    from src.pipeline.fallback import (
        create_pipeline_restore_point,
        create_timestamped_pipeline_restore_point,
        restore_pipeline_restore_point,
        rollback_pipeline_test,
    )

    from src.pipeline.extract_to_staging import extract_emails_to_staging
    from src.pipeline.load_staging_to_db import load_staging_to_db
    from src.pipeline.update_fact_sales import update_fact_sales
    from src.pipeline.move_processed_file import move_processed_files


logger = get_logger(__name__)


# ============================================================
# CONFIG (imported from settings.py)
# ============================================================


# ============================================================
# HELPERS
# ============================================================


def run_step(step_name: str, func, *args, **kwargs):
    """
    Chạy 1 step có log thời gian.
    """

    logger.info("")
    logger.info("=" * 80)
    logger.info("START STEP | %s", step_name)
    logger.info("=" * 80)

    start = time.perf_counter()

    try:
        result = func(*args, **kwargs)

        elapsed = time.perf_counter() - start

        logger.info("=" * 80)
        logger.info("END STEP SUCCESS | %s | elapsed=%s", step_name, format_seconds(elapsed))
        logger.info("=" * 80)

        return result

    except Exception as e:
        elapsed = time.perf_counter() - start

        logger.exception(
            "END STEP FAILED | %s | elapsed=%s | error=%s",
            step_name,
            format_seconds(elapsed),
            e,
        )

        raise


def print_step_summary(title: str, summary: dict | None) -> None:
    if not summary:
        return

    print("")
    print(title)

    for key, value in summary.items():
        if key == "output_paths":
            continue

        print(f"- {key}: {value}")


# ============================================================
# PIPELINE SERVICE
# ============================================================

def run_pipeline(
    input_dir: str | Path = DEFAULT_INPUT_DIR,
    staging_dir: str | Path = DEFAULT_STAGING_DIR,
    quality_check_dir: str | Path = DEFAULT_QUALITY_CHECK_DIR,
    success_dir: str | Path = DEFAULT_SUCCESS_DIR,
    failed_dir: str | Path = DEFAULT_FAILED_DIR,
    limit: int | None = None,
    skip_restore_point: bool = False,
    timestamp_restore_point: bool = False,
    dry_run: bool = False,
    skip_update_fact: bool = False,
    refresh_fact_all: bool = False,
    skip_move_files: bool = False,
    move_on_limit: bool = False,
    rollback_on_fail: bool = False,
) -> dict:
    """
    Chạy pipeline end-to-end.

    Args:
        dry_run:
            - extract_to_staging vẫn xuất CSV.
            - load_staging_to_db chạy transaction rồi rollback.
            - update_fact_sales và move_processed_file bị skip.
        limit:
            - dùng để test một số lượng email nhỏ.
            - nếu có limit thì mặc định không move file, trừ khi bật move_on_limit.
    """

    pipeline_start = time.perf_counter()

    restore_point_path = None
    move_step_started = False

    summary = {
        "started_at": now_text(),
        "restore_point": None,
        "extract": None,
        "load_db": None,
        "update_fact_sales": None,
        "move_files": None,
        "dry_run": dry_run,
        "success": False,
        "error": "",
        "elapsed": "",
    }

    logger.info("=" * 100)
    logger.info("TNBIKE PIPELINE STARTED")
    logger.info("=" * 100)
    logger.info("Input dir             : %s", resolve_project_path(input_dir))
    logger.info("Staging dir           : %s", resolve_project_path(staging_dir))
    logger.info("Quality check dir     : %s", resolve_project_path(quality_check_dir))
    logger.info("Success dir           : %s", resolve_project_path(success_dir))
    logger.info("Failed dir            : %s", resolve_project_path(failed_dir))
    logger.info("Limit                 : %s", limit)
    logger.info("Skip restore point    : %s", skip_restore_point)
    logger.info("Timestamp restore     : %s", timestamp_restore_point)
    logger.info("Dry run               : %s", dry_run)
    logger.info("Skip update fact      : %s", skip_update_fact)
    logger.info("Refresh fact all      : %s", refresh_fact_all)
    logger.info("Skip move files       : %s", skip_move_files)
    logger.info("Move on limit         : %s", move_on_limit)
    logger.info("Rollback on fail      : %s", rollback_on_fail)
    logger.info("=" * 100)

    try:
        # ----------------------------------------------------
        # STEP 0: Create restore point
        # ----------------------------------------------------
        if not skip_restore_point:
            if timestamp_restore_point:
                restore_point_path = run_step(
                    "CREATE TIMESTAMPED RESTORE POINT",
                    create_timestamped_pipeline_restore_point,
                )
            else:
                restore_point_path = run_step(
                    "CREATE RESTORE POINT",
                    create_pipeline_restore_point,
                )

            summary["restore_point"] = str(restore_point_path)

        else:
            logger.warning("Restore point was skipped by user option")

        # ----------------------------------------------------
        # STEP 1: Extract to staging
        # ----------------------------------------------------
        summary["extract"] = run_step(
            "EXTRACT EMAILS TO STAGING",
            extract_emails_to_staging,
            input_dir=input_dir,
            output_dir=staging_dir,
            quality_check_dir=quality_check_dir,
            limit=limit,
        )

        # ----------------------------------------------------
        # STEP 2: Load staging to DB
        # ----------------------------------------------------
        summary["load_db"] = run_step(
            "LOAD STAGING TO DB",
            load_staging_to_db,
            staging_dir=staging_dir,
            dry_run=dry_run,
        )

        # ----------------------------------------------------
        # If dry run, stop after load_db
        # ----------------------------------------------------
        if dry_run:
            logger.warning(
                "DRY RUN enabled: skip update_fact_sales and move_processed_file because DB load was rolled back"
            )

            summary["success"] = True
            return summary

        # ----------------------------------------------------
        # STEP 3: Update fact_sales
        # ----------------------------------------------------
        if not skip_update_fact:
            summary["update_fact_sales"] = run_step(
                "UPDATE FACT SALES",
                update_fact_sales,
                staging_dir=staging_dir,
                refresh_all=refresh_fact_all,
                dry_run=False,
            )
        else:
            logger.warning("Update fact_sales was skipped by user option")

        # ----------------------------------------------------
        # STEP 4: Move processed files
        # ----------------------------------------------------
        should_move_files = not skip_move_files

        if limit is not None and limit > 0 and not move_on_limit:
            should_move_files = False
            logger.warning(
                "Move files skipped because --limit is used. "
                "Use --move-on-limit if you really want to move files during limited test."
            )

        if should_move_files:
            move_step_started = True

            summary["move_files"] = run_step(
                "MOVE PROCESSED FILES",
                move_processed_files,
                input_dir=input_dir,
                success_dir=success_dir,
                failed_dir=failed_dir,
                email_log_file=Path(staging_dir) / "staging_email_log.csv",
                extract_fail_file=Path(quality_check_dir) / "extract_fail.csv",
                overwrite=False,
                copy_only=False,
                dry_run=False,
            )
        else:
            logger.warning("Move processed files was skipped")

        summary["success"] = True

        return summary

    except Exception as e:
        summary["success"] = False
        summary["error"] = str(e)

        logger.exception("TNBIKE PIPELINE FAILED: %s", e)

        if rollback_on_fail and restore_point_path:
            logger.warning("Rollback on fail enabled. Starting rollback...")

            try:
                if move_step_started:
                    run_step(
                        "ROLLBACK DB AND FILES",
                        rollback_pipeline_test,
                        restore_point=restore_point_path,
                        restore_db=True,
                        move_files=True,
                    )
                else:
                    run_step(
                        "RESTORE DB RESTORE POINT",
                        restore_pipeline_restore_point,
                        input_path=restore_point_path,
                    )

                logger.warning("Rollback finished")

            except Exception as rollback_error:
                logger.exception("Rollback failed: %s", rollback_error)

        raise

    finally:
        elapsed = time.perf_counter() - pipeline_start
        summary["elapsed"] = format_seconds(elapsed)

        logger.info("=" * 100)
        logger.info("TNBIKE PIPELINE FINISHED")
        logger.info("Success : %s", summary["success"])
        logger.info("Elapsed : %s", summary["elapsed"])
        logger.info("Error   : %s", summary["error"])
        logger.info("=" * 100)


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run TNBIKE ETL pipeline end-to-end"
    )

    parser.add_argument(
        "--input-dir",
        default=DEFAULT_INPUT_DIR,
        help="Input folder containing .eml files",
    )

    parser.add_argument(
        "--staging-dir",
        default=DEFAULT_STAGING_DIR,
        help="Folder for staging CSV files",
    )

    parser.add_argument(
        "--quality-check-dir",
        default=DEFAULT_QUALITY_CHECK_DIR,
        help="Folder for quality check CSV files",
    )

    parser.add_argument(
        "--success-dir",
        default=DEFAULT_SUCCESS_DIR,
        help="Folder to move successful .eml files",
    )

    parser.add_argument(
        "--failed-dir",
        default=DEFAULT_FAILED_DIR,
        help="Folder to move failed .eml files",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of .eml files for testing",
    )

    parser.add_argument(
        "--skip-restore-point",
        action="store_true",
        help="Do not create DB restore point before running pipeline",
    )

    parser.add_argument(
        "--timestamp-restore-point",
        action="store_true",
        help="Create timestamped restore point instead of overwriting default restore point",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run extract and load DB dry-run, then skip fact update and file move",
    )

    parser.add_argument(
        "--skip-update-fact",
        action="store_true",
        help="Skip update_fact_sales step",
    )

    parser.add_argument(
        "--refresh-fact-all",
        action="store_true",
        help="Rebuild all fact_sales instead of only current staging SO numbers",
    )

    parser.add_argument(
        "--skip-move-files",
        action="store_true",
        help="Do not move processed .eml files after successful pipeline",
    )

    parser.add_argument(
        "--move-on-limit",
        action="store_true",
        help="Allow moving files even when --limit is used",
    )

    parser.add_argument(
        "--rollback-on-fail",
        action="store_true",
        help="Automatically restore DB restore point if pipeline fails",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="run_pipeline.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        summary = run_pipeline(
            input_dir=args.input_dir,
            staging_dir=args.staging_dir,
            quality_check_dir=args.quality_check_dir,
            success_dir=args.success_dir,
            failed_dir=args.failed_dir,
            limit=args.limit,
            skip_restore_point=args.skip_restore_point,
            timestamp_restore_point=args.timestamp_restore_point,
            dry_run=args.dry_run,
            skip_update_fact=args.skip_update_fact,
            refresh_fact_all=args.refresh_fact_all,
            skip_move_files=args.skip_move_files,
            move_on_limit=args.move_on_limit,
            rollback_on_fail=args.rollback_on_fail,
        )

        print("")
        print("RUN PIPELINE SUCCESS")
        print(f"Started at       : {summary['started_at']}")
        print(f"Elapsed          : {summary['elapsed']}")
        print(f"Dry run          : {summary['dry_run']}")
        print(f"Restore point    : {summary['restore_point']}")

        print_step_summary("Extract summary:", summary.get("extract"))
        print_step_summary("Load DB summary:", summary.get("load_db"))
        print_step_summary("Update fact summary:", summary.get("update_fact_sales"))
        print_step_summary("Move file summary:", summary.get("move_files"))

    except Exception as e:
        logger.exception("RUN PIPELINE FAILED: %s", e)

        print("")
        print("RUN PIPELINE FAILED")
        print(f"Error: {e}")
        print("")
        print("Nếu cần fallback thủ công, chạy:")
        print("py -m src.pipeline.fallback rollback")

        sys.exit(1)


if __name__ == "__main__":
    main()
# ============================================================
# src/preprocessing/run_preprocessing.py
# Orchestrate preprocessing / master data standardization
#
# Mục đích:
#   Chạy các bước chuẩn hóa dữ liệu master trước hoặc sau khi reset DB.
#
# Flow mặc định:
#   1. Tạo DB restore point
#   2. standardize_province.py
#   3. map_customer_province.py --update-db --reset-before-update
#   4. standardize_color.py --update-db
#   5. update_fact_sales.py --all
#
# Không nên chạy file này mỗi lần xử lý email mới.
# Pipeline email hằng ngày dùng:
#   py -m src.pipeline.run_pipeline
# ============================================================

import sys
import time
import argparse
import subprocess
from pathlib import Path
from datetime import datetime


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.config.logging_config import setup_logging, get_logger
    from src.utils.file_utils import resolve_project_path
    from src.utils.time_utils import now_text, format_seconds
    from src.pipeline.fallback import create_pipeline_restore_point

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.config.logging_config import setup_logging, get_logger
    from src.utils.file_utils import resolve_project_path
    from src.utils.time_utils import now_text, format_seconds
    from src.pipeline.fallback import create_pipeline_restore_point


logger = get_logger(__name__)


# ============================================================
# HELPERS
# ============================================================

def module_exists(module_path: str) -> bool:
    """
    Check nhanh file module có tồn tại không.

    Example:
        src.preprocessing.standardize_province
        -> src/preprocessing/standardize_province.py
    """

    relative_path = module_path.replace(".", "/") + ".py"
    full_path = resolve_project_path(relative_path)

    return full_path.exists()


def run_command(
    step_name: str,
    command: list[str],
    dry_run: bool = False,
) -> dict:
    """
    Chạy 1 command dạng subprocess.

    Dùng subprocess thay vì import function trực tiếp để tránh phụ thuộc tên hàm
    bên trong từng file preprocessing.
    """

    logger.info("")
    logger.info("=" * 80)
    logger.info("START STEP | %s", step_name)
    logger.info("=" * 80)
    logger.info("Command: %s", " ".join(command))

    start = time.perf_counter()

    if dry_run:
        elapsed = time.perf_counter() - start

        logger.warning("DRY RUN: command was not executed")
        logger.info("END STEP DRY RUN | %s | elapsed=%s", step_name, format_seconds(elapsed))

        return {
            "step": step_name,
            "command": " ".join(command),
            "status": "DRY_RUN",
            "returncode": None,
            "elapsed": format_seconds(elapsed),
        }

    try:
        completed = subprocess.run(
            command,
            check=True,
            text=True,
        )

        elapsed = time.perf_counter() - start

        logger.info("=" * 80)
        logger.info("END STEP SUCCESS | %s | elapsed=%s", step_name, format_seconds(elapsed))
        logger.info("=" * 80)

        return {
            "step": step_name,
            "command": " ".join(command),
            "status": "SUCCESS",
            "returncode": completed.returncode,
            "elapsed": format_seconds(elapsed),
        }

    except subprocess.CalledProcessError as e:
        elapsed = time.perf_counter() - start

        logger.exception(
            "END STEP FAILED | %s | elapsed=%s | returncode=%s",
            step_name,
            format_seconds(elapsed),
            e.returncode,
        )

        return {
            "step": step_name,
            "command": " ".join(command),
            "status": "FAILED",
            "returncode": e.returncode,
            "elapsed": format_seconds(elapsed),
        }


def build_module_command(module_name: str, args: list[str] | None = None) -> list[str]:
    if args is None:
        args = []

    return [
        sys.executable,
        "-m",
        module_name,
        *args,
    ]


# ============================================================
# MAIN SERVICE
# ============================================================

def run_preprocessing(
    create_restore_point: bool = True,
    dry_run: bool = False,
    continue_on_error: bool = False,

    skip_province: bool = False,
    skip_customer_province: bool = False,
    skip_color: bool = False,
    skip_product_line: bool = True,
    skip_update_fact: bool = False,

    only_missing_customer_province: bool = False,
    reset_customer_province: bool = True,
    refresh_fact_all: bool = True,
) -> dict:
    """
    Chạy preprocessing tổng.

    Default:
        - Chạy province
        - Map customer province
        - Chuẩn hóa color
        - Rebuild fact_sales toàn bộ

    Không chạy product line mặc định vì tùy project có/không có file map_product_line.py.
    """

    start = time.perf_counter()

    summary = {
        "started_at": now_text(),
        "restore_point": None,
        "dry_run": dry_run,
        "success": False,
        "steps": [],
        "error": "",
        "elapsed": "",
    }

    logger.info("=" * 100)
    logger.info("TNBIKE PREPROCESSING STARTED")
    logger.info("=" * 100)
    logger.info("Create restore point        : %s", create_restore_point)
    logger.info("Dry run                     : %s", dry_run)
    logger.info("Continue on error           : %s", continue_on_error)
    logger.info("Skip province               : %s", skip_province)
    logger.info("Skip customer province      : %s", skip_customer_province)
    logger.info("Skip color                  : %s", skip_color)
    logger.info("Skip product line           : %s", skip_product_line)
    logger.info("Skip update fact            : %s", skip_update_fact)
    logger.info("Only missing cust province  : %s", only_missing_customer_province)
    logger.info("Reset customer province     : %s", reset_customer_province)
    logger.info("Refresh fact all            : %s", refresh_fact_all)
    logger.info("=" * 100)

    try:
        # ----------------------------------------------------
        # STEP 0: restore point
        # ----------------------------------------------------
        if create_restore_point:
            if dry_run:
                logger.warning("DRY RUN: restore point was not created")
                summary["restore_point"] = "DRY_RUN"
            else:
                restore_point = create_pipeline_restore_point()
                summary["restore_point"] = str(restore_point)

        # ----------------------------------------------------
        # Build preprocessing steps
        # ----------------------------------------------------
        steps = []

        if not skip_province:
            steps.append(
                (
                    "STANDARDIZE PROVINCE",
                    build_module_command("src.preprocessing.standardize_province"),
                    "src.preprocessing.standardize_province",
                )
            )

        if not skip_customer_province:
            customer_args = ["--update-db"]

            if only_missing_customer_province:
                customer_args.append("--only-missing")

            if reset_customer_province:
                customer_args.append("--reset-before-update")

            steps.append(
                (
                    "MAP CUSTOMER PROVINCE",
                    build_module_command(
                        "src.preprocessing.map_customer_province",
                        customer_args,
                    ),
                    "src.preprocessing.map_customer_province",
                )
            )

        if not skip_color:
            steps.append(
                (
                    "STANDARDIZE COLOR",
                    build_module_command(
                        "src.preprocessing.standardize_color",
                        ["--update-db"],
                    ),
                    "src.preprocessing.standardize_color",
                )
            )

        if not skip_product_line:
            steps.append(
                (
                    "MAP PRODUCT LINE",
                    build_module_command(
                        "src.preprocessing.map_product_line",
                        ["--update-db"],
                    ),
                    "src.preprocessing.map_product_line",
                )
            )

        if not skip_update_fact:
            fact_args = []

            if refresh_fact_all:
                fact_args.append("--all")

            steps.append(
                (
                    "UPDATE FACT SALES",
                    build_module_command(
                        "src.pipeline.update_fact_sales",
                        fact_args,
                    ),
                    "src.pipeline.update_fact_sales",
                )
            )

        # ----------------------------------------------------
        # Run steps
        # ----------------------------------------------------
        for step_name, command, module_name in steps:
            if not module_exists(module_name):
                result = {
                    "step": step_name,
                    "command": " ".join(command),
                    "status": "SKIPPED",
                    "returncode": None,
                    "elapsed": "0.00s",
                    "reason": f"Module not found: {module_name}",
                }

                logger.warning(
                    "SKIP STEP | %s | Module not found: %s",
                    step_name,
                    module_name,
                )

                summary["steps"].append(result)

                if not continue_on_error:
                    raise FileNotFoundError(f"Module not found: {module_name}")

                continue

            result = run_command(
                step_name=step_name,
                command=command,
                dry_run=dry_run,
            )

            summary["steps"].append(result)

            if result["status"] == "FAILED" and not continue_on_error:
                raise RuntimeError(f"Preprocessing step failed: {step_name}")

        summary["success"] = True
        return summary

    except Exception as e:
        summary["success"] = False
        summary["error"] = str(e)

        logger.exception("TNBIKE PREPROCESSING FAILED: %s", e)

        raise

    finally:
        elapsed = time.perf_counter() - start
        summary["elapsed"] = format_seconds(elapsed)

        logger.info("=" * 100)
        logger.info("TNBIKE PREPROCESSING FINISHED")
        logger.info("Success : %s", summary["success"])
        logger.info("Elapsed : %s", summary["elapsed"])
        logger.info("Error   : %s", summary["error"])
        logger.info("=" * 100)


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run TNBIKE preprocessing / master data standardization"
    )

    parser.add_argument(
        "--no-restore-point",
        action="store_true",
        help="Do not create DB restore point before preprocessing",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands only, do not execute preprocessing",
    )

    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue running next steps even if one step fails",
    )

    parser.add_argument(
        "--skip-province",
        action="store_true",
        help="Skip standardize_province step",
    )

    parser.add_argument(
        "--skip-customer-province",
        action="store_true",
        help="Skip map_customer_province step",
    )

    parser.add_argument(
        "--only-missing-customer-province",
        action="store_true",
        help="Only map customers with province_id IS NULL",
    )

    parser.add_argument(
        "--no-reset-customer-province",
        action="store_true",
        help="Do not reset all customer.province_id before mapping",
    )

    parser.add_argument(
        "--skip-color",
        action="store_true",
        help="Skip standardize_color step",
    )

    parser.add_argument(
        "--skip-update-fact",
        action="store_true",
        help="Skip update_fact_sales step",
    )

    parser.add_argument(
        "--no-refresh-fact-all",
        action="store_true",
        help="Do not pass --all to update_fact_sales",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="run_preprocessing.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        summary = run_preprocessing(
            create_restore_point=not args.no_restore_point,
            dry_run=args.dry_run,
            continue_on_error=args.continue_on_error,

            skip_province=args.skip_province,
            skip_customer_province=args.skip_customer_province,
            skip_color=args.skip_color,
            skip_update_fact=args.skip_update_fact,

            only_missing_customer_province=args.only_missing_customer_province,
            reset_customer_province=not args.no_reset_customer_province,
            refresh_fact_all=not args.no_refresh_fact_all,
        )

        print("")
        print("RUN PREPROCESSING SUCCESS")
        print(f"Started at    : {summary['started_at']}")
        print(f"Elapsed       : {summary['elapsed']}")
        print(f"Dry run       : {summary['dry_run']}")
        print(f"Restore point : {summary['restore_point']}")
        print("")

        print("Steps:")
        for step in summary["steps"]:
            print(
                f"- {step['step']}: {step['status']} "
                f"| elapsed={step['elapsed']} "
                f"| command={step['command']}"
            )

    except Exception as e:
        logger.exception("RUN PREPROCESSING FAILED: %s", e)

        print("")
        print("RUN PREPROCESSING FAILED")
        print(f"Error: {e}")
        print("")
        print("Nếu cần fallback DB, chạy:")
        print("py -m src.pipeline.fallback restore-db")

        sys.exit(1)


if __name__ == "__main__":
    main()
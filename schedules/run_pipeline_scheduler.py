# ============================================================
# schedules/run_pipeline_scheduler.py
# Run TNBIKE pipeline based on schedule config
#
# Modes:
#   manual
#   watch
#   interval
#   fixed_time
# ============================================================

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, date


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from src.config.logging_config import setup_logging, get_logger
from src.config.settings import (
    ensure_project_dirs,
    resolve_project_path,
    PIPELINE_SCHEDULE_CONFIG,
)
from src.pipeline.run_pipeline import run_pipeline


logger = get_logger(__name__)


# ============================================================
# YAML LOADER
# ============================================================

def load_yaml_config(path: str | Path) -> dict:
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError(
            "Thiếu thư viện PyYAML. Cài bằng lệnh: pip install pyyaml"
        ) from e

    path = resolve_project_path(path)

    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy config schedule: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data


# ============================================================
# HELPERS
# ============================================================

def clean_mode(value: str | None) -> str:
    value = str(value or "manual").strip().lower()

    if value not in {"manual", "watch", "interval", "fixed_time"}:
        raise ValueError(
            f"Schedule mode không hợp lệ: {value}. "
            f"Chỉ hỗ trợ: manual, watch, interval, fixed_time"
        )

    return value


def get_pipeline_kwargs(config: dict) -> dict:
    pipeline = config.get("pipeline", {}) or {}

    return {
        "input_dir": pipeline.get("input_dir", "data/incoming/eml"),
        "staging_dir": pipeline.get("staging_dir", "data/processed/staging"),
        "quality_check_dir": pipeline.get("quality_check_dir", "data/processed/quality_check"),
        "success_dir": pipeline.get("success_dir", "data/processed/success_eml/eml"),
        "failed_dir": pipeline.get("failed_dir", "data/processed/failed_eml/eml"),

        "limit": pipeline.get("limit"),

        "skip_restore_point": bool(pipeline.get("skip_restore_point", False)),
        "timestamp_restore_point": bool(pipeline.get("timestamp_restore_point", False)),
        "dry_run": bool(pipeline.get("dry_run", False)),

        "skip_update_fact": bool(pipeline.get("skip_update_fact", False)),
        "refresh_fact_all": bool(pipeline.get("refresh_fact_all", False)),

        "skip_move_files": bool(pipeline.get("skip_move_files", False)),
        "move_on_limit": bool(pipeline.get("move_on_limit", False)),

        "rollback_on_fail": bool(pipeline.get("rollback_on_fail", True)),
    }


def run_pipeline_from_config(config: dict) -> dict:
    kwargs = get_pipeline_kwargs(config)

    logger.info("Running pipeline from schedule config...")
    logger.info("Pipeline kwargs: %s", kwargs)

    return run_pipeline(**kwargs)


def has_eml_files(input_dir: str | Path, file_pattern: str = "*.eml") -> bool:
    input_dir = resolve_project_path(input_dir)

    if not input_dir.exists():
        return False

    return any(input_dir.glob(file_pattern))


def snapshot_files(input_dir: str | Path, file_pattern: str = "*.eml") -> dict[str, int]:
    input_dir = resolve_project_path(input_dir)

    if not input_dir.exists():
        return {}

    result = {}

    for file_path in input_dir.glob(file_pattern):
        if file_path.is_file():
            result[str(file_path)] = file_path.stat().st_size

    return result


def wait_until_files_stable(
    input_dir: str | Path,
    file_pattern: str = "*.eml",
    stable_seconds: int = 5,
) -> bool:
    """
    Tránh chạy pipeline khi file đang copy dở.
    """

    before = snapshot_files(input_dir, file_pattern)

    if not before:
        return False

    time.sleep(stable_seconds)

    after = snapshot_files(input_dir, file_pattern)

    return before == after and bool(after)


def weekday_key(today: date | None = None) -> str:
    today = today or date.today()

    keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return keys[today.weekday()]


def current_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


# ============================================================
# MODES
# ============================================================

def run_manual(config: dict) -> None:
    logger.info("Schedule mode: manual")
    run_pipeline_from_config(config)


def run_watch(config: dict) -> None:
    logger.info("Schedule mode: watch")

    pipeline = config.get("pipeline", {}) or {}
    watch = config.get("watch", {}) or {}

    input_dir = pipeline.get("input_dir", "data/incoming/eml")
    poll_seconds = int(watch.get("poll_seconds", 10))
    stable_seconds = int(watch.get("stable_seconds", 5))
    file_pattern = watch.get("file_pattern", "*.eml")

    logger.info("Watching folder: %s", resolve_project_path(input_dir))
    logger.info("Poll seconds  : %s", poll_seconds)
    logger.info("Stable seconds: %s", stable_seconds)
    logger.info("File pattern  : %s", file_pattern)

    while True:
        try:
            if has_eml_files(input_dir, file_pattern):
                logger.info("Detected .eml files in incoming folder")

                if wait_until_files_stable(
                    input_dir=input_dir,
                    file_pattern=file_pattern,
                    stable_seconds=stable_seconds,
                ):
                    run_pipeline_from_config(config)
                else:
                    logger.info("Files are not stable yet. Waiting next poll...")

            time.sleep(poll_seconds)

        except KeyboardInterrupt:
            logger.warning("Scheduler stopped by user")
            break

        except Exception as e:
            logger.exception("Watch scheduler error: %s", e)
            time.sleep(poll_seconds)


def run_interval(config: dict) -> None:
    logger.info("Schedule mode: interval")

    interval = config.get("interval", {}) or {}
    every_minutes = int(interval.get("every_minutes", 60))
    sleep_seconds = every_minutes * 60

    logger.info("Run every %s minutes", every_minutes)

    while True:
        try:
            run_pipeline_from_config(config)
            logger.info("Sleeping %s seconds until next run", sleep_seconds)
            time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            logger.warning("Scheduler stopped by user")
            break

        except Exception as e:
            logger.exception("Interval scheduler error: %s", e)
            time.sleep(sleep_seconds)


def run_fixed_time(config: dict) -> None:
    logger.info("Schedule mode: fixed_time")

    fixed_time = config.get("fixed_time", {}) or {}
    times = fixed_time.get("times", []) or []
    days = fixed_time.get("days", []) or ["mon", "tue", "wed", "thu", "fri"]

    times = [str(item).strip() for item in times if str(item).strip()]
    days = [str(item).strip().lower() for item in days if str(item).strip()]

    if not times:
        raise RuntimeError("fixed_time.times đang rỗng trong pipeline_schedule.yaml")

    logger.info("Run times: %s", times)
    logger.info("Run days : %s", days)

    ran_keys = set()

    while True:
        try:
            today = date.today()
            day_key = weekday_key(today)
            now_key = current_hhmm()

            if day_key in days and now_key in times:
                run_key = f"{today.isoformat()}_{now_key}"

                if run_key not in ran_keys:
                    logger.info("Matched fixed time: %s", run_key)
                    run_pipeline_from_config(config)
                    ran_keys.add(run_key)

            time.sleep(30)

        except KeyboardInterrupt:
            logger.warning("Scheduler stopped by user")
            break

        except Exception as e:
            logger.exception("Fixed-time scheduler error: %s", e)
            time.sleep(60)


# ============================================================
# MAIN
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run TNBIKE pipeline scheduler"
    )

    parser.add_argument(
        "--config",
        default=str(PIPELINE_SCHEDULE_CONFIG),
        help="Path to pipeline schedule YAML config",
    )

    parser.add_argument(
        "--mode",
        default=None,
        choices=["manual", "watch", "interval", "fixed_time"],
        help="Override mode in YAML config",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="pipeline_scheduler.log",
        error_log_file="error.log",
    )

    ensure_project_dirs()

    args = parse_args()
    config = load_yaml_config(args.config)

    if not bool(config.get("enabled", True)):
        logger.warning("Scheduler config is disabled: enabled=false")
        print("Scheduler disabled by config.")
        return

    mode = clean_mode(args.mode or config.get("mode", "manual"))

    logger.info("=" * 80)
    logger.info("TNBIKE PIPELINE SCHEDULER STARTED")
    logger.info("Config: %s", resolve_project_path(args.config))
    logger.info("Mode  : %s", mode)
    logger.info("=" * 80)

    if mode == "manual":
        run_manual(config)

    elif mode == "watch":
        run_watch(config)

    elif mode == "interval":
        run_interval(config)

    elif mode == "fixed_time":
        run_fixed_time(config)


if __name__ == "__main__":
    main()
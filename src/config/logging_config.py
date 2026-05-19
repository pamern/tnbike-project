# ============================================================
# src/config/logging_config.py
# Cấu hình logging tập trung cho TNBIKE Pipeline
# ============================================================

import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime


# ============================================================
# PROJECT PATH
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


# ============================================================
# DEFAULT CONFIG
# ============================================================

DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

DEFAULT_PIPELINE_LOG = "pipeline.log"
DEFAULT_ERROR_LOG = "error.log"


# ============================================================
# FORMATTER
# ============================================================

class PipelineFormatter(logging.Formatter):
    """
    Formatter chuẩn cho toàn bộ pipeline.
    """

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "step"):
            record.step = "-"

        if not hasattr(record, "so_number"):
            record.so_number = "-"

        return super().format(record)


# ============================================================
# SETUP LOGGING
# ============================================================

def setup_logging(
    log_dir: str | Path = DEFAULT_LOG_DIR,
    log_level: int | str = DEFAULT_LOG_LEVEL,
    pipeline_log_file: str = DEFAULT_PIPELINE_LOG,
    error_log_file: str = DEFAULT_ERROR_LOG,
    enable_console: bool = True,
    enable_file: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """
    Cấu hình logging dùng chung cho toàn project.

    Args:
        log_dir: thư mục lưu log
        log_level: logging.INFO / DEBUG / WARNING...
        pipeline_log_file: file log chính
        error_log_file: file log lỗi
        enable_console: có in log ra terminal không
        enable_file: có ghi log ra file không
        max_bytes: dung lượng tối đa mỗi file log
        backup_count: số file log backup giữ lại
    """

    log_dir = Path(log_dir)

    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir

    log_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(log_level, str):
        log_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Xóa handler cũ để tránh log bị lặp nhiều lần
    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = PipelineFormatter(
        fmt=DEFAULT_LOG_FORMAT,
        datefmt=DEFAULT_DATE_FORMAT,
    )

    handlers = []

    # Console handler
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    # File handler chính
    if enable_file:
        pipeline_handler = RotatingFileHandler(
            filename=log_dir / pipeline_log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        pipeline_handler.setLevel(log_level)
        pipeline_handler.setFormatter(formatter)
        handlers.append(pipeline_handler)

        error_handler = RotatingFileHandler(
            filename=log_dir / error_log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        handlers.append(error_handler)

    for handler in handlers:
        root_logger.addHandler(handler)

    logging.getLogger(__name__).info("Logging initialized. Log dir: %s", log_dir)


# ============================================================
# GET LOGGER
# ============================================================

def get_logger(name: str) -> logging.Logger:
    """
    Lấy logger theo tên module.

    Usage:
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)


# ============================================================
# PIPELINE LOG HELPERS
# ============================================================

def log_section(title: str, char: str = "=", length: int = 70) -> None:
    """
    In một section lớn trong log.
    """
    logger = logging.getLogger("pipeline")

    logger.info(char * length)
    logger.info(title)
    logger.info(char * length)


def log_step_start(step_name: str) -> datetime:
    """
    Log bắt đầu một step.
    Trả về start_time để dùng tính duration.
    """
    logger = logging.getLogger("pipeline")
    start_time = datetime.now()

    logger.info("-" * 70)
    logger.info("START: %s", step_name)
    logger.info("-" * 70)

    return start_time


def log_step_end(step_name: str, start_time: datetime) -> None:
    """
    Log kết thúc một step.
    """
    logger = logging.getLogger("pipeline")
    duration = datetime.now() - start_time

    logger.info("DONE : %s | Duration: %.2fs", step_name, duration.total_seconds())


def log_step_failed(step_name: str, error: Exception) -> None:
    """
    Log lỗi của một step.
    """
    logger = logging.getLogger("pipeline")

    logger.exception("FAILED: %s | Error: %s", step_name, error)


def log_pipeline_summary(summary: dict) -> None:
    """
    Log tổng kết pipeline dạng key-value.
    """
    logger = logging.getLogger("pipeline")

    logger.info("=" * 70)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 70)

    for key, value in summary.items():
        logger.info("%-30s: %s", key, value)


# ============================================================
# MANUAL TEST
# ============================================================

if __name__ == "__main__":
    setup_logging(log_level="DEBUG")

    logger = get_logger(__name__)

    log_section("TEST LOGGING CONFIG")

    start = log_step_start("Demo step")

    logger.debug("Đây là log DEBUG")
    logger.info("Đây là log INFO")
    logger.warning("Đây là log WARNING")

    try:
        result = 1 / 0
    except Exception as e:
        log_step_failed("Demo step", e)

    log_step_end("Demo step", start)

    log_pipeline_summary(
        {
            "found_eml_files": 1132,
            "success_orders": 1130,
            "failed_orders": 2,
            "output_log": "logs/pipeline.log",
        }
    )
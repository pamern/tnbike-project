from pathlib import Path
import subprocess
import sys
import csv
import shutil
import time
import logging
import os


# ============================================================
# PATH CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INCOMING_EML_DIR = PROJECT_ROOT / "data" / "incoming" / "eml"
PROCESSED_EML_DIR = PROJECT_ROOT / "data" / "processed" / "eml"
FAILED_EML_DIR = PROJECT_ROOT / "data" / "failed" / "eml"

STAGING_DIR = PROJECT_ROOT / "data" / "staging"
LOG_DIR = PROJECT_ROOT / "logs"

STAGING_FAIL_CSV = STAGING_DIR / "staging_fail.csv"

LOG_FILE = LOG_DIR / "run_pipeline.log"
LOCK_FILE = STAGING_DIR / "pipeline.lock"


# ============================================================
# BASIC SETUP
# ============================================================

def setup_dirs() -> None:
    for path in [
        INCOMING_EML_DIR,
        PROCESSED_EML_DIR,
        FAILED_EML_DIR,
        STAGING_DIR,
        LOG_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    setup_dirs()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def acquire_lock() -> None:
    """
    Chống chạy 2 pipeline cùng lúc.
    """
    if LOCK_FILE.exists():
        raise RuntimeError(
            f"Pipeline đang bị lock: {LOCK_FILE}\n"
            f"Nếu chắc chắn không còn pipeline nào đang chạy, hãy xóa file này rồi chạy lại."
        )

    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")


def release_lock() -> None:
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


# ============================================================
# HELPERS
# ============================================================

def run_step(command: list[str], step_name: str) -> None:
    logging.info("=" * 70)
    logging.info(step_name)
    logging.info("Running command: " + " ".join(command))
    logging.info("=" * 70)

    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
    )

    logging.info(f"DONE: {step_name}")


def get_incoming_eml_files() -> list[Path]:
    return sorted(INCOMING_EML_DIR.glob("*.eml"))


def read_failed_email_files() -> set[str]:
    """
    Đọc staging_fail.csv để biết file email nào có lỗi.
    Nếu một email có lỗi header/line/customer/product thì đưa sang failed để kiểm tra.
    """
    failed_files = set()

    if not STAGING_FAIL_CSV.exists():
        return failed_files

    with open(STAGING_FAIL_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            return failed_files

        if "source_email_file" not in reader.fieldnames:
            logging.warning(
                f"{STAGING_FAIL_CSV} không có cột source_email_file, bỏ qua bước phân loại failed."
            )
            return failed_files

        for row in reader:
            source_email_file = str(row.get("source_email_file", "")).strip()
            if source_email_file:
                failed_files.add(source_email_file)

    return failed_files


def safe_move(source: Path, target_dir: Path) -> Path | None:
    """
    Move file an toàn:
    - Nếu file nguồn không còn tồn tại thì bỏ qua.
    - Nếu file đích đã tồn tại thì thêm timestamp để tránh ghi đè.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        logging.warning(f"Không move vì file nguồn không tồn tại: {source}")
        return None

    target_path = target_dir / source.name

    if target_path.exists():
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        target_path = target_dir / f"{source.stem}_{timestamp}{source.suffix}"

    shutil.move(str(source), str(target_path))
    return target_path


def move_eml_files_after_success(original_eml_files: list[Path]) -> None:
    """
    Sau khi extract + import + refresh thành công:
    - File có lỗi trong staging_fail.csv -> failed/eml
    - File không có lỗi -> processed/eml

    Lưu ý:
    Nếu một đơn có dữ liệu import được nhưng vẫn có lỗi dòng hàng, file sẽ sang failed để kiểm tra.
    Đây là cách an toàn hơn cho data quality.
    """
    failed_file_names = read_failed_email_files()

    processed_count = 0
    failed_count = 0

    for eml_path in original_eml_files:
        if eml_path.name in failed_file_names:
            moved_path = safe_move(eml_path, FAILED_EML_DIR)
            if moved_path:
                failed_count += 1
                logging.info(f"Moved failed EML: {moved_path}")
        else:
            moved_path = safe_move(eml_path, PROCESSED_EML_DIR)
            if moved_path:
                processed_count += 1
                logging.info(f"Moved processed EML: {moved_path}")

    logging.info(f"Processed EML moved: {processed_count}")
    logging.info(f"Failed EML moved   : {failed_count}")


def print_staging_summary() -> None:
    files = [
        "staging_email_log.csv",
        "staging_customer.csv",
        "staging_customer_log.csv",
        "staging_sales_order.csv",
        "staging_order_line.csv",
        "staging_fail.csv",
        "staging_fail_summary.csv",
    ]

    logging.info("STAGING SUMMARY")

    for file_name in files:
        path = STAGING_DIR / file_name

        if not path.exists():
            logging.info(f"{file_name}: not found")
            continue

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                row_count = max(sum(1 for _ in f) - 1, 0)

            logging.info(f"{file_name}: {row_count} rows")

        except Exception as e:
            logging.warning(f"Không đọc được {file_name}: {e}")


# ============================================================
# MAIN PIPELINE
# ============================================================

def run_pipeline() -> None:
    setup_dirs()

    original_eml_files = get_incoming_eml_files()

    if not original_eml_files:
        logging.info(f"Không có file .eml nào trong {INCOMING_EML_DIR}")
        return

    logging.info(f"Tìm thấy {len(original_eml_files)} file .eml trong incoming.")

    # STEP 1: Extract EML + PDF attachment -> staging CSV
    run_step(
        [sys.executable, "src/pipeline/extract_orders_from_email.py"],
        "STEP 1 - Extract EML attachment PDF to staging",
    )

    print_staging_summary()

    # STEP 2: Import staging CSV -> PostgreSQL
    run_step(
        [sys.executable, "src/pipeline/load_staging_to_postgres.py"],
        "STEP 2 - Import staging to PostgreSQL",
    )

    # STEP 3: Refresh fact_sales theo staging_sales_order.csv
    run_step(
        [sys.executable, "src/pipeline/refresh_fact_sales.py"],
        "STEP 3 - Refresh fact_sales",
    )

    # STEP 4: Move EML files sau khi DB import + refresh thành công
    move_eml_files_after_success(original_eml_files)

    logging.info("PIPELINE COMPLETED SUCCESSFULLY")


def main() -> None:
    setup_logging()

    try:
        acquire_lock()
        run_pipeline()

    except subprocess.CalledProcessError as e:
        logging.error("PIPELINE FAILED")
        logging.error(f"Command failed: {e}")
        logging.error(
            "File .eml vẫn được giữ trong data/incoming/eml để bạn kiểm tra và chạy lại."
        )
        raise

    except Exception as e:
        logging.error("PIPELINE FAILED")
        logging.error(str(e))
        logging.error(
            "File .eml vẫn được giữ trong data/incoming/eml để bạn kiểm tra và chạy lại."
        )
        raise

    finally:
        release_lock()


if __name__ == "__main__":
    main()
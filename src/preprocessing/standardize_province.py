# ============================================================
# src/preprocessing/standardize_province.py
# Chạy file sql/04_standardize_province.sql để chuẩn hóa bảng province
# ============================================================

import sys
from pathlib import Path
import psycopg2


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.database.connection import get_db_config, DB_SCHEMA
    from src.utils.file_utils import resolve_project_path
    from src.config.logging_config import setup_logging, get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.database.connection import get_db_config, DB_SCHEMA
    from src.utils.file_utils import resolve_project_path
    from src.config.logging_config import setup_logging, get_logger


logger = get_logger(__name__)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_SQL_FILE = "sql/04_standardize_province.sql"


# ============================================================
# CORE FUNCTIONS
# ============================================================

def read_sql_file(sql_file: str | Path = DEFAULT_SQL_FILE) -> str:
    """
    Đọc nội dung file SQL UTF-8.
    """

    sql_path = resolve_project_path(sql_file)

    if not sql_path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    logger.info("Reading SQL file: %s", sql_path)

    return sql_path.read_text(encoding="utf-8")


def execute_sql_file(sql_file: str | Path = DEFAULT_SQL_FILE) -> None:
    """
    Chạy toàn bộ nội dung file SQL.

    Lưu ý:
        File SQL đã có BEGIN / COMMIT nên connection dùng autocommit=True
        để tránh lỗi transaction lồng nhau.
    """

    sql_text = read_sql_file(sql_file)
    db_config = get_db_config()

    logger.info("Connecting to database...")
    logger.info("Target schema: %s", DB_SCHEMA)

    conn = None

    try:
        conn = psycopg2.connect(**db_config)
        conn.set_client_encoding("UTF8")

        # Vì file SQL có BEGIN; COMMIT; nên bật autocommit
        conn.autocommit = True

        with conn.cursor() as cur:
            logger.info("Executing province standardization SQL...")
            cur.execute(sql_text)

        logger.info("Province standardization SQL executed successfully")

    except Exception as e:
        logger.exception("Failed to execute province standardization SQL: %s", e)
        raise

    finally:
        if conn is not None:
            conn.close()
            logger.info("Database connection closed")


def check_province_result() -> dict:
    """
    Kiểm tra kết quả sau khi chuẩn hóa province.
    """

    db_config = get_db_config()

    query = f"""
        SET search_path TO {DB_SCHEMA}, public;

        SELECT
            COUNT(*) AS province_count,
            COUNT(*) FILTER (WHERE region = 'Miền Bắc') AS mien_bac,
            COUNT(*) FILTER (WHERE region = 'Miền Trung') AS mien_trung,
            COUNT(*) FILTER (WHERE region = 'Miền Nam') AS mien_nam
        FROM province;
    """

    conn = None

    try:
        conn = psycopg2.connect(**db_config)
        conn.set_client_encoding("UTF8")

        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()

        result = {
            "province_count": row[0],
            "mien_bac": row[1],
            "mien_trung": row[2],
            "mien_nam": row[3],
        }

        return result

    finally:
        if conn is not None:
            conn.close()


def standardize_province(sql_file: str | Path = DEFAULT_SQL_FILE) -> dict:
    """
    Hàm chính để module khác có thể import dùng lại.
    """

    logger.info("=" * 70)
    logger.info("STANDARDIZE PROVINCE STARTED")
    logger.info("=" * 70)

    execute_sql_file(sql_file)

    result = check_province_result()

    logger.info("=" * 70)
    logger.info("STANDARDIZE PROVINCE SUCCESS")
    logger.info("Province count : %s", result["province_count"])
    logger.info("Miền Bắc       : %s", result["mien_bac"])
    logger.info("Miền Trung     : %s", result["mien_trung"])
    logger.info("Miền Nam       : %s", result["mien_nam"])
    logger.info("=" * 70)

    return result


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="standardize_province.log",
        error_log_file="error.log",
    )

    try:
        result = standardize_province()

        print("")
        print("STANDARDIZE PROVINCE SUCCESS")
        print(f"Province count : {result['province_count']}")
        print(f"Miền Bắc       : {result['mien_bac']}")
        print(f"Miền Trung     : {result['mien_trung']}")
        print(f"Miền Nam       : {result['mien_nam']}")

    except Exception as e:
        logger.exception("STANDARDIZE PROVINCE FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
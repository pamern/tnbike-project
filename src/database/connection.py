# ============================================================
# src/database/connection.py
# Quản lý kết nối PostgreSQL cho TNBIKE Pipeline
# ============================================================

import os
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Any

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv


# ============================================================
# LOAD ENV
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH)

logger = logging.getLogger(__name__)


# ============================================================
# DB CONFIG
# Hỗ trợ cả chuẩn PG* và DB* để tiện dùng
# ============================================================

DB_HOST = os.getenv("PGHOST") or os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("PGPORT") or os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("PGDATABASE") or os.getenv("DB_NAME", "tnbike_db")
DB_USER = os.getenv("PGUSER") or os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("PGPASSWORD") or os.getenv("DB_PASSWORD", "postgres")
DB_SCHEMA = os.getenv("PGSCHEMA") or os.getenv("DB_SCHEMA", "tnbike")

DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", "1"))
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", "5"))


_connection_pool: Optional[pool.SimpleConnectionPool] = None


# ============================================================
# CONNECTION PARAMS
# ============================================================

def get_db_config() -> dict:
    """
    Trả về cấu hình kết nối database.
    """
    return {
        "host": DB_HOST,
        "port": DB_PORT,
        "database": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "client_encoding": "UTF8",
    }


# ============================================================
# POOL MANAGEMENT
# ============================================================

def init_connection_pool() -> None:
    """
    Khởi tạo connection pool.
    Gọi một lần khi pipeline bắt đầu.
    """
    global _connection_pool

    if _connection_pool is not None:
        return

    try:
        _connection_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=DB_MIN_CONN,
            maxconn=DB_MAX_CONN,
            **get_db_config(),
        )

        logger.info(
            "Initialized PostgreSQL connection pool: %s:%s/%s, schema=%s",
            DB_HOST,
            DB_PORT,
            DB_NAME,
            DB_SCHEMA,
        )

    except Exception as e:
        logger.exception("Cannot initialize PostgreSQL connection pool")
        raise e


def close_connection_pool() -> None:
    """
    Đóng toàn bộ connection trong pool.
    Gọi khi pipeline kết thúc.
    """
    global _connection_pool

    if _connection_pool is not None:
        _connection_pool.closeall()
        _connection_pool = None
        logger.info("Closed PostgreSQL connection pool")


# ============================================================
# CONNECTION CONTEXT
# ============================================================

@contextmanager
def get_connection():
    """
    Lấy connection từ pool.
    Tự commit nếu thành công, rollback nếu lỗi.

    Usage:
        with get_connection() as conn:
            ...
    """
    global _connection_pool

    if _connection_pool is None:
        init_connection_pool()

    conn = None

    try:
        conn = _connection_pool.getconn()
        conn.autocommit = False

        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {DB_SCHEMA}, public;")
            cur.execute("SET client_encoding TO 'UTF8';")

        yield conn

        conn.commit()

    except Exception as e:
        if conn is not None:
            conn.rollback()

        logger.exception("Database transaction failed")
        raise e

    finally:
        if conn is not None and _connection_pool is not None:
            _connection_pool.putconn(conn)


@contextmanager
def get_cursor(dict_cursor: bool = False):
    """
    Lấy cursor trực tiếp.

    Args:
        dict_cursor=True  -> trả kết quả dạng dict
        dict_cursor=False -> trả kết quả dạng tuple

    Usage:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT * FROM customer LIMIT 5")
            rows = cur.fetchall()
    """
    with get_connection() as conn:
        cursor_factory = RealDictCursor if dict_cursor else None

        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur


# ============================================================
# COMMON HELPERS
# ============================================================

def test_connection() -> bool:
    """
    Kiểm tra kết nối database.
    """
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT
                    current_database() AS database_name,
                    current_schema() AS schema_name,
                    version() AS postgres_version;
            """)
            result = cur.fetchone()

        logger.info(
            "Database connected: database=%s, schema=%s",
            result["database_name"],
            result["schema_name"],
        )

        return True

    except Exception:
        logger.exception("Database connection test failed")
        return False


def fetch_one(query: str, params: Optional[tuple] = None) -> Optional[Any]:
    """
    Chạy SELECT và trả về 1 dòng dạng dict.
    """
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def fetch_all(query: str, params: Optional[tuple] = None) -> list:
    """
    Chạy SELECT và trả về nhiều dòng dạng list[dict].
    """
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def execute_query(query: str, params: Optional[tuple] = None) -> int:
    """
    Chạy INSERT / UPDATE / DELETE.
    Trả về số dòng bị ảnh hưởng.
    """
    with get_cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def execute_many(query: str, params_list: list[tuple]) -> int:
    """
    Chạy executemany cho nhiều dòng.
    Dùng khi insert/update batch nhỏ-vừa.
    """
    if not params_list:
        return 0

    with get_cursor() as cur:
        cur.executemany(query, params_list)
        return cur.rowcount


def table_exists(table_name: str, schema: str = DB_SCHEMA) -> bool:
    """
    Kiểm tra bảng có tồn tại không.
    """
    query = """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
        ) AS exists_flag;
    """

    result = fetch_one(query, (schema, table_name))
    return bool(result["exists_flag"]) if result else False


def get_table_row_count(table_name: str) -> int:
    """
    Đếm số dòng của một bảng trong schema hiện tại.
    """
    query = f"SELECT COUNT(*) AS row_count FROM {DB_SCHEMA}.{table_name};"
    result = fetch_one(query)
    return int(result["row_count"]) if result else 0


# ============================================================
# MANUAL TEST
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    ok = test_connection()

    if ok:
        print("Database connection OK")

        for table in ["customer", "product", "sales_order", "order_line", "fact_sales"]:
            if table_exists(table):
                print(f"{table}: {get_table_row_count(table):,} rows")
            else:
                print(f"{table}: not found")

    close_connection_pool()
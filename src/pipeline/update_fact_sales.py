# ============================================================
# src/pipeline/update_fact_sales.py
# Update / refresh fact_sales after loading staging to DB
#
# Default:
#   - Đọc so_number từ data/processed/staging/staging_sales_order.csv
#   - Xóa fact_sales của các so_number đó
#   - Insert lại từ sales_order + order_line + dimensions
#
# Optional:
#   --all
#       Rebuild toàn bộ fact_sales
#
#   --dry-run
#       Chạy thử rồi rollback
# ============================================================

import csv
import sys
import argparse
from pathlib import Path


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.database.connection import get_connection, DB_SCHEMA
    from src.utils.file_utils import resolve_project_path
    from src.config.logging_config import setup_logging, get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.database.connection import get_connection, DB_SCHEMA
    from src.utils.file_utils import resolve_project_path
    from src.config.logging_config import setup_logging, get_logger


logger = get_logger(__name__)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_STAGING_DIR = "data/processed/staging"
STAGING_SALES_ORDER_FILE = "staging_sales_order.csv"


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value) -> str:
    if value is None:
        return ""

    return str(value).strip()


def get_staging_sales_order_path(
    staging_dir: str | Path = DEFAULT_STAGING_DIR,
) -> Path:
    staging_dir = resolve_project_path(staging_dir)
    return staging_dir / STAGING_SALES_ORDER_FILE


def read_so_numbers_from_staging(
    staging_dir: str | Path = DEFAULT_STAGING_DIR,
) -> list[str]:
    """
    Đọc danh sách so_number từ staging_sales_order.csv.
    """

    path = get_staging_sales_order_path(staging_dir)

    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {path}")

    so_numbers = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or "so_number" not in reader.fieldnames:
            raise RuntimeError(f"File {path} thiếu cột so_number")

        for row in reader:
            so_number = clean_text(row.get("so_number"))

            if so_number:
                so_numbers.append(so_number)

    so_numbers = sorted(set(so_numbers))

    logger.info("Loaded so_number from staging: %s", len(so_numbers))

    return so_numbers


# ============================================================
# VALIDATION
# ============================================================

def validate_so_numbers_exist(
    cur,
    so_numbers: list[str],
) -> None:
    """
    Kiểm tra so_number đã có trong sales_order sau bước load_staging_to_db.
    """

    if not so_numbers:
        return

    cur.execute(
        f"""
        SELECT x.so_number
        FROM unnest(%s::text[]) AS x(so_number)
        LEFT JOIN {DB_SCHEMA}.sales_order so
            ON so.so_number = x.so_number
        WHERE so.so_number IS NULL
        LIMIT 20;
        """,
        (so_numbers,),
    )

    missing_rows = cur.fetchall()

    if missing_rows:
        raise RuntimeError(
            f"Có so_number chưa tồn tại trong sales_order, ví dụ: {missing_rows}"
        )


def validate_fact_sales_source_for_so_numbers(
    cur,
    so_numbers: list[str],
) -> None:
    """
    Kiểm tra các đơn cần refresh có order_line để insert vào fact_sales.
    """

    if not so_numbers:
        return

    cur.execute(
        f"""
        SELECT so.so_number
        FROM {DB_SCHEMA}.sales_order so
        LEFT JOIN {DB_SCHEMA}.order_line ol
            ON ol.order_id = so.order_id
        WHERE so.so_number = ANY(%s)
        GROUP BY so.so_number
        HAVING COUNT(ol.line_id) = 0
        LIMIT 20;
        """,
        (so_numbers,),
    )

    empty_orders = cur.fetchall()

    if empty_orders:
        raise RuntimeError(
            f"Có sales_order chưa có order_line, ví dụ: {empty_orders}"
        )


# ============================================================
# REFRESH FACT SALES - BATCH
# ============================================================

def delete_fact_sales_for_so_numbers(
    cur,
    so_numbers: list[str],
) -> int:
    if not so_numbers:
        return 0

    cur.execute(
        f"""
        DELETE FROM {DB_SCHEMA}.fact_sales
        WHERE so_number = ANY(%s);
        """,
        (so_numbers,),
    )

    return cur.rowcount


def insert_fact_sales_for_so_numbers(
    cur,
    so_numbers: list[str],
) -> int:
    if not so_numbers:
        return 0

    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.fact_sales (
            order_date,
            fiscal_year,
            fiscal_quarter,
            fiscal_month,
            week_of_year,
            so_number,
            order_id,
            line_id,
            customer_code,
            customer_name,
            province_id,
            province_name,
            region,
            product_code,
            product_name,
            color,
            line_id_fk,
            line_name,
            group_code,
            group_name,
            quantity,
            unit_price,
            line_total
        )
        SELECT
            so.order_date,
            EXTRACT(YEAR FROM so.order_date)::SMALLINT AS fiscal_year,
            EXTRACT(QUARTER FROM so.order_date)::SMALLINT AS fiscal_quarter,
            EXTRACT(MONTH FROM so.order_date)::SMALLINT AS fiscal_month,
            EXTRACT(WEEK FROM so.order_date)::SMALLINT AS week_of_year,

            so.so_number,
            so.order_id,
            ol.line_id,

            c.customer_code,
            c.customer_name,

            p.province_id,
            p.province_name,
            p.region,

            pr.product_code,
            pr.product_name,
            pr.color,

            pr.line_id AS line_id_fk,
            pl.line_name,

            pg.group_code,
            pg.group_name,

            ol.quantity,
            ol.unit_price,
            ol.line_total
        FROM {DB_SCHEMA}.order_line ol
        JOIN {DB_SCHEMA}.sales_order so
            ON so.order_id = ol.order_id
        JOIN {DB_SCHEMA}.customer c
            ON c.customer_code = so.customer_code
        LEFT JOIN {DB_SCHEMA}.province p
            ON p.province_id = c.province_id
        JOIN {DB_SCHEMA}.product pr
            ON pr.product_code = ol.product_code
        LEFT JOIN {DB_SCHEMA}.product_line pl
            ON pl.line_id = pr.line_id
        LEFT JOIN {DB_SCHEMA}.product_group pg
            ON pg.group_code = pl.group_code
        WHERE so.so_number = ANY(%s);
        """,
        (so_numbers,),
    )

    return cur.rowcount


def refresh_fact_sales_for_so_numbers(
    cur,
    so_numbers: list[str],
) -> dict:
    """
    Refresh fact_sales theo danh sách so_number trong batch.
    """

    if not so_numbers:
        return {
            "so_numbers": 0,
            "deleted": 0,
            "inserted": 0,
        }

    validate_so_numbers_exist(cur, so_numbers)
    validate_fact_sales_source_for_so_numbers(cur, so_numbers)

    deleted_count = delete_fact_sales_for_so_numbers(cur, so_numbers)
    inserted_count = insert_fact_sales_for_so_numbers(cur, so_numbers)

    return {
        "so_numbers": len(so_numbers),
        "deleted": deleted_count,
        "inserted": inserted_count,
    }


# ============================================================
# REBUILD FACT SALES - ALL
# ============================================================

def rebuild_fact_sales_all(cur) -> dict:
    """
    Rebuild toàn bộ fact_sales.

    Dùng khi reset DB, chuẩn hóa province/product/color hoặc muốn làm mới toàn bộ.
    """

    cur.execute(f"DELETE FROM {DB_SCHEMA}.fact_sales;")
    deleted_count = cur.rowcount

    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.fact_sales (
            order_date,
            fiscal_year,
            fiscal_quarter,
            fiscal_month,
            week_of_year,
            so_number,
            order_id,
            line_id,
            customer_code,
            customer_name,
            province_id,
            province_name,
            region,
            product_code,
            product_name,
            color,
            line_id_fk,
            line_name,
            group_code,
            group_name,
            quantity,
            unit_price,
            line_total
        )
        SELECT
            so.order_date,
            EXTRACT(YEAR FROM so.order_date)::SMALLINT AS fiscal_year,
            EXTRACT(QUARTER FROM so.order_date)::SMALLINT AS fiscal_quarter,
            EXTRACT(MONTH FROM so.order_date)::SMALLINT AS fiscal_month,
            EXTRACT(WEEK FROM so.order_date)::SMALLINT AS week_of_year,

            so.so_number,
            so.order_id,
            ol.line_id,

            c.customer_code,
            c.customer_name,

            p.province_id,
            p.province_name,
            p.region,

            pr.product_code,
            pr.product_name,
            pr.color,

            pr.line_id AS line_id_fk,
            pl.line_name,

            pg.group_code,
            pg.group_name,

            ol.quantity,
            ol.unit_price,
            ol.line_total
        FROM {DB_SCHEMA}.order_line ol
        JOIN {DB_SCHEMA}.sales_order so
            ON so.order_id = ol.order_id
        JOIN {DB_SCHEMA}.customer c
            ON c.customer_code = so.customer_code
        LEFT JOIN {DB_SCHEMA}.province p
            ON p.province_id = c.province_id
        JOIN {DB_SCHEMA}.product pr
            ON pr.product_code = ol.product_code
        LEFT JOIN {DB_SCHEMA}.product_line pl
            ON pl.line_id = pr.line_id
        LEFT JOIN {DB_SCHEMA}.product_group pg
            ON pg.group_code = pl.group_code;
        """
    )

    inserted_count = cur.rowcount

    return {
        "deleted": deleted_count,
        "inserted": inserted_count,
    }


# ============================================================
# VERIFY
# ============================================================

def verify_fact_sales_for_so_numbers(
    cur,
    so_numbers: list[str],
) -> dict:
    if not so_numbers:
        return {
            "fact_rows": 0,
            "total_amount": 0,
            "total_quantity": 0,
        }

    cur.execute(
        f"""
        SELECT
            COUNT(*) AS fact_rows,
            COALESCE(SUM(line_total), 0) AS total_amount,
            COALESCE(SUM(quantity), 0) AS total_quantity
        FROM {DB_SCHEMA}.fact_sales
        WHERE so_number = ANY(%s);
        """,
        (so_numbers,),
    )

    fact_rows, total_amount, total_quantity = cur.fetchone()

    return {
        "fact_rows": fact_rows,
        "total_amount": total_amount,
        "total_quantity": total_quantity,
    }


def verify_fact_sales_all(cur) -> dict:
    cur.execute(
        f"""
        SELECT
            COUNT(*) AS fact_rows,
            COALESCE(SUM(line_total), 0) AS total_amount,
            COALESCE(SUM(quantity), 0) AS total_quantity
        FROM {DB_SCHEMA}.fact_sales;
        """
    )

    fact_rows, total_amount, total_quantity = cur.fetchone()

    return {
        "fact_rows": fact_rows,
        "total_amount": total_amount,
        "total_quantity": total_quantity,
    }


# ============================================================
# MAIN SERVICE
# ============================================================

def update_fact_sales(
    staging_dir: str | Path = DEFAULT_STAGING_DIR,
    refresh_all: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Update fact_sales.

    Default:
        refresh theo so_number trong staging_sales_order.csv.

    refresh_all=True:
        rebuild toàn bộ fact_sales.
    """

    logger.info("=" * 80)
    logger.info("UPDATE FACT SALES STARTED")
    logger.info("=" * 80)
    logger.info("Schema      : %s", DB_SCHEMA)
    logger.info("Staging dir : %s", resolve_project_path(staging_dir))
    logger.info("Refresh all : %s", refresh_all)
    logger.info("Dry run     : %s", dry_run)
    logger.info("=" * 80)

    so_numbers = []

    if not refresh_all:
        so_numbers = read_so_numbers_from_staging(staging_dir)

        if not so_numbers:
            logger.warning("Không có so_number nào cần refresh fact_sales.")

            return {
                "mode": "batch",
                "so_numbers": 0,
                "deleted": 0,
                "inserted": 0,
                "dry_run": dry_run,
                "verify": {},
            }

    summary = {
        "mode": "all" if refresh_all else "batch",
        "so_numbers": len(so_numbers),
        "deleted": 0,
        "inserted": 0,
        "dry_run": dry_run,
        "verify": {},
    }

    try:
        with get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET search_path TO {DB_SCHEMA}, public;")

                    if refresh_all:
                        stats = rebuild_fact_sales_all(cur)
                        verify = verify_fact_sales_all(cur)
                    else:
                        stats = refresh_fact_sales_for_so_numbers(
                            cur,
                            so_numbers,
                        )

                        verify = verify_fact_sales_for_so_numbers(
                            cur,
                            so_numbers,
                        )

                    summary["deleted"] = stats["deleted"]
                    summary["inserted"] = stats["inserted"]
                    summary["verify"] = verify

                if dry_run:
                    conn.rollback()
                    logger.warning("DRY RUN: transaction rolled back")
                else:
                    conn.commit()
                    logger.info("Transaction committed")

            except Exception:
                conn.rollback()
                logger.exception("UPDATE FACT SALES FAILED")
                raise

    except Exception:
        raise

    logger.info("=" * 80)
    logger.info("UPDATE FACT SALES FINISHED")
    logger.info("Mode       : %s", summary["mode"])
    logger.info("SO numbers : %s", summary["so_numbers"])
    logger.info("Deleted    : %s", summary["deleted"])
    logger.info("Inserted   : %s", summary["inserted"])
    logger.info("Dry run    : %s", summary["dry_run"])
    logger.info("Verify     : %s", summary["verify"])
    logger.info("=" * 80)

    return summary


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update TNBIKE fact_sales table"
    )

    parser.add_argument(
        "--staging-dir",
        default=DEFAULT_STAGING_DIR,
        help="Folder containing staging_sales_order.csv",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Rebuild all fact_sales instead of only staging so_numbers",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run update in transaction then rollback",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="update_fact_sales.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        summary = update_fact_sales(
            staging_dir=args.staging_dir,
            refresh_all=args.all,
            dry_run=args.dry_run,
        )

        verify = summary.get("verify", {})

        print("")
        print("UPDATE FACT SALES SUCCESS")
        print(f"Mode              : {summary['mode']}")
        print(f"SO numbers         : {summary['so_numbers']}")
        print(f"Deleted            : {summary['deleted']}")
        print(f"Inserted           : {summary['inserted']}")
        print(f"Dry run            : {summary['dry_run']}")

        if verify:
            print(f"Verify fact rows   : {verify['fact_rows']}")
            print(f"Verify amount      : {verify['total_amount']}")
            print(f"Verify quantity    : {verify['total_quantity']}")

    except Exception as e:
        logger.exception("UPDATE FACT SALES FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
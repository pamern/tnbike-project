# ============================================================
# src/pipeline/load_staging_to_db.py
# Load staging CSV -> PostgreSQL
#
# Input:
#   data/processed/staging/staging_email_log.csv
#   data/processed/staging/staging_customer.csv
#   data/processed/staging/staging_sales_order.csv
#   data/processed/staging/staging_order_line.csv
#
# Notes:
#   - email_log: upsert theo message_id
#   - customer: upsert customer mới theo customer_code
#   - sales_order: upsert theo so_number
#   - order_line: xóa line cũ của các so_number trong batch rồi insert lại
# ============================================================

import csv
import sys
import argparse
from pathlib import Path
from decimal import Decimal

from psycopg2.extras import execute_values


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

EMAIL_LOG_CSV = "staging_email_log.csv"
STAGING_CUSTOMER_CSV = "staging_customer.csv"
SALES_ORDER_CSV = "staging_sales_order.csv"
ORDER_LINE_CSV = "staging_order_line.csv"


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value) -> str:
    if value is None:
        return ""

    return str(value).strip()


def empty_to_none(value):
    value = clean_text(value)
    return value if value else None


def to_bool_or_default(value, default=True) -> bool:
    value = clean_text(value).lower()

    if value in {"true", "1", "yes", "y", "t"}:
        return True

    if value in {"false", "0", "no", "n", "f"}:
        return False

    return default


def to_decimal(value):
    value = empty_to_none(value)

    if value is None:
        return None

    return Decimal(str(value))


def read_csv_rows(path: str | Path, required: bool = True) -> list[dict]:
    path = resolve_project_path(path)

    if not path.exists():
        if required:
            raise FileNotFoundError(f"Không tìm thấy file: {path}")

        logger.warning("Optional CSV not found, skipped: %s", path)
        return []

    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def require_columns(path: str | Path, rows: list[dict], required_columns: list[str]) -> None:
    if not rows:
        return

    actual = set(rows[0].keys())
    missing = [col for col in required_columns if col not in actual]

    if missing:
        raise RuntimeError(
            f"File {path} thiếu cột: {missing}. "
            f"Cột hiện có: {sorted(actual)}"
        )


def get_staging_paths(staging_dir: str | Path = DEFAULT_STAGING_DIR) -> dict[str, Path]:
    staging_dir = resolve_project_path(staging_dir)

    return {
        "email_log": staging_dir / EMAIL_LOG_CSV,
        "customer": staging_dir / STAGING_CUSTOMER_CSV,
        "sales_order": staging_dir / SALES_ORDER_CSV,
        "order_line": staging_dir / ORDER_LINE_CSV,
    }


# ============================================================
# LOAD CSV ROWS
# ============================================================

def load_email_log_rows(path: str | Path) -> list[tuple]:
    rows = read_csv_rows(path, required=True)

    require_columns(
        path,
        rows,
        [
            "message_id",
            "from_address",
            "received_at",
            "attachment_name",
            "processing_status",
        ],
    )

    # Dedup theo message_id để tránh lỗi:
    # ON CONFLICT DO UPDATE command cannot affect row a second time
    by_message_id = {}

    for row in rows:
        message_id = empty_to_none(row.get("message_id"))

        # message_id UNIQUE. Nếu rỗng thì không import DB.
        if not message_id:
            continue

        by_message_id[message_id] = (
            message_id,
            empty_to_none(row.get("from_address")),
            empty_to_none(row.get("received_at")),
            empty_to_none(row.get("attachment_name")),
            empty_to_none(row.get("processing_status")),
            empty_to_none(row.get("processing_reason")),
            empty_to_none(row.get("processed_at")),
            empty_to_none(row.get("updated_at")),
        )

    return list(by_message_id.values())


def load_staging_customer_rows(path: str | Path) -> list[tuple]:
    rows = read_csv_rows(path, required=False)

    require_columns(
        path,
        rows,
        [
            "customer_code",
            "customer_name",
            "tax_code",
            "address",
            "province_id",
            "customer_tier",
            "is_active",
        ],
    )

    output = []
    seen_customer_codes = set()

    for row in rows:
        customer_code = empty_to_none(row.get("customer_code"))
        customer_name = empty_to_none(row.get("customer_name"))

        if not customer_code or not customer_name:
            raise RuntimeError(
                "staging_customer có dòng thiếu NOT NULL: "
                f"customer_code={customer_code}, customer_name={customer_name}"
            )

        if customer_code in seen_customer_codes:
            continue

        seen_customer_codes.add(customer_code)

        output.append(
            (
                customer_code,
                customer_name,
                empty_to_none(row.get("tax_code")),
                empty_to_none(row.get("address")),
                empty_to_none(row.get("province_id")),
                empty_to_none(row.get("customer_tier")) or "STANDARD",
                to_bool_or_default(row.get("is_active"), default=True),
            )
        )

    return output


def load_sales_order_rows(path: str | Path) -> list[tuple]:
    rows = read_csv_rows(path, required=True)

    require_columns(
        path,
        rows,
        [
            "so_number",
            "invoice_symbol",
            "invoice_number",
            "order_date",
            "customer_code",
        ],
    )

    output = []
    seen_so_numbers = set()

    for row in rows:
        so_number = empty_to_none(row.get("so_number"))
        order_date = empty_to_none(row.get("order_date"))
        customer_code = empty_to_none(row.get("customer_code"))

        if not so_number or not order_date or not customer_code:
            raise RuntimeError(
                "staging_sales_order có dòng thiếu NOT NULL: "
                f"so_number={so_number}, order_date={order_date}, customer_code={customer_code}"
            )

        if so_number in seen_so_numbers:
            raise RuntimeError(f"Trùng so_number trong {path}: {so_number}")

        seen_so_numbers.add(so_number)

        output.append(
            (
                so_number,
                empty_to_none(row.get("invoice_symbol")),
                empty_to_none(row.get("invoice_number")),
                order_date,
                customer_code,
            )
        )

    return output


def load_order_line_rows(path: str | Path) -> list[tuple]:
    rows = read_csv_rows(path, required=True)

    require_columns(
        path,
        rows,
        [
            "order_id",
            "so_number",
            "product_code",
            "quantity",
            "unit_price",
            "line_total",
        ],
    )

    output = []

    for row in rows:
        so_number = empty_to_none(row.get("so_number"))
        product_code = empty_to_none(row.get("product_code"))
        quantity = to_decimal(row.get("quantity"))
        unit_price = to_decimal(row.get("unit_price"))
        line_total = to_decimal(row.get("line_total"))

        if not so_number or not product_code or quantity is None or unit_price is None or line_total is None:
            raise RuntimeError(
                "staging_order_line có dòng thiếu NOT NULL: "
                f"so_number={so_number}, product_code={product_code}, "
                f"quantity={quantity}, unit_price={unit_price}, line_total={line_total}"
            )

        output.append(
            (
                so_number,
                product_code,
                quantity,
                unit_price,
                line_total,
            )
        )

    return output


# ============================================================
# ENSURE LOG TABLE
# ============================================================

def ensure_email_log_table(cur) -> None:
    """
    Đảm bảo bảng email_log có schema mới.

    Nếu sql/03_create_email_log.sql đã chạy rồi thì hàm này không làm hại gì.
    Nếu bảng cũ còn created_at thì không rename ở đây; chỉ thêm cột mới nếu thiếu.
    """

    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DB_SCHEMA}.email_log (
            email_log_id BIGSERIAL PRIMARY KEY,
            message_id TEXT UNIQUE,
            from_address TEXT,
            received_at TIMESTAMPTZ,
            attachment_name TEXT,
            processing_status TEXT,
            processing_reason TEXT,
            processed_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )

    cur.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.email_log
        ADD COLUMN IF NOT EXISTS processing_reason TEXT;
        """
    )

    cur.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.email_log
        ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ DEFAULT NOW();
        """
    )

    cur.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.email_log
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
        """
    )

    cur.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_email_log_status
        ON {DB_SCHEMA}.email_log(processing_status);
        """
    )

    cur.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_email_log_reason
        ON {DB_SCHEMA}.email_log(processing_reason);
        """
    )

    cur.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_email_log_processed_at
        ON {DB_SCHEMA}.email_log(processed_at);
        """
    )


# ============================================================
# IMPORT EMAIL LOG / MASTER DATA
# ============================================================

def import_email_log(cur, rows: list[tuple]) -> int:
    if not rows:
        return 0

    execute_values(
        cur,
        f"""
        INSERT INTO {DB_SCHEMA}.email_log (
            message_id,
            from_address,
            received_at,
            attachment_name,
            processing_status,
            processing_reason,
            processed_at,
            updated_at
        )
        VALUES %s
        ON CONFLICT (message_id) DO UPDATE
        SET
            from_address = EXCLUDED.from_address,
            received_at = EXCLUDED.received_at,
            attachment_name = EXCLUDED.attachment_name,
            processing_status = EXCLUDED.processing_status,
            processing_reason = EXCLUDED.processing_reason,
            processed_at = COALESCE(EXCLUDED.processed_at, NOW()),
            updated_at = COALESCE(EXCLUDED.updated_at, NOW());
        """,
        rows,
    )

    return len(rows)


def import_staging_customer(cur, rows: list[tuple]) -> int:
    if not rows:
        return 0

    execute_values(
        cur,
        f"""
        INSERT INTO {DB_SCHEMA}.customer (
            customer_code,
            customer_name,
            tax_code,
            address,
            province_id,
            customer_tier,
            is_active
        )
        VALUES %s
        ON CONFLICT (customer_code) DO UPDATE
        SET
            customer_name = EXCLUDED.customer_name,
            tax_code = EXCLUDED.tax_code,
            address = EXCLUDED.address,
            province_id = EXCLUDED.province_id,
            customer_tier = EXCLUDED.customer_tier,
            is_active = EXCLUDED.is_active,
            updated_at = NOW();
        """,
        rows,
    )

    return len(rows)


# ============================================================
# TEMP TABLES
# ============================================================

def create_temp_tables(cur) -> None:
    cur.execute(
        """
        CREATE TEMP TABLE tmp_sales_order (
            so_number       VARCHAR(20),
            invoice_symbol  VARCHAR(15),
            invoice_number  VARCHAR(20),
            order_date      DATE,
            customer_code   VARCHAR(20)
        ) ON COMMIT DROP;

        CREATE TEMP TABLE tmp_order_line (
            so_number       VARCHAR(20),
            product_code    VARCHAR(20),
            quantity        NUMERIC(10,2),
            unit_price      NUMERIC(15,2),
            line_total      NUMERIC(15,2)
        ) ON COMMIT DROP;
        """
    )


def bulk_insert_temp_sales_order(cur, rows: list[tuple]) -> int:
    if not rows:
        return 0

    execute_values(
        cur,
        """
        INSERT INTO tmp_sales_order (
            so_number,
            invoice_symbol,
            invoice_number,
            order_date,
            customer_code
        )
        VALUES %s;
        """,
        rows,
    )

    return len(rows)


def bulk_insert_temp_order_line(cur, rows: list[tuple]) -> int:
    if not rows:
        return 0

    execute_values(
        cur,
        """
        INSERT INTO tmp_order_line (
            so_number,
            product_code,
            quantity,
            unit_price,
            line_total
        )
        VALUES %s;
        """,
        rows,
    )

    return len(rows)


# ============================================================
# VALIDATION
# ============================================================

def validate_temp_data(cur) -> None:
    """
    Validate dữ liệu staging trước khi ghi transaction vào DB.
    """

    cur.execute(
        f"""
        SELECT tso.customer_code
        FROM tmp_sales_order tso
        LEFT JOIN {DB_SCHEMA}.customer c
            ON c.customer_code = tso.customer_code
        WHERE c.customer_code IS NULL
        LIMIT 10;
        """
    )

    missing_customers = cur.fetchall()

    if missing_customers:
        raise RuntimeError(
            f"Vẫn còn customer_code không tồn tại trong DB, ví dụ: {missing_customers}"
        )

    cur.execute(
        f"""
        SELECT tol.product_code
        FROM tmp_order_line tol
        LEFT JOIN {DB_SCHEMA}.product p
            ON p.product_code = tol.product_code
        WHERE p.product_code IS NULL
        LIMIT 10;
        """
    )

    missing_products = cur.fetchall()

    if missing_products:
        raise RuntimeError(
            f"Vẫn còn product_code không tồn tại trong DB, ví dụ: {missing_products}"
        )

    cur.execute(
        """
        SELECT tol.so_number
        FROM tmp_order_line tol
        LEFT JOIN tmp_sales_order tso
            ON tso.so_number = tol.so_number
        WHERE tso.so_number IS NULL
        LIMIT 10;
        """
    )

    orphan_lines = cur.fetchall()

    if orphan_lines:
        raise RuntimeError(
            f"Có order_line không có sales_order tương ứng trong staging, ví dụ: {orphan_lines}"
        )

    cur.execute(
        """
        SELECT tso.so_number
        FROM tmp_sales_order tso
        LEFT JOIN tmp_order_line tol
            ON tol.so_number = tso.so_number
        WHERE tol.so_number IS NULL
        LIMIT 10;
        """
    )

    empty_orders = cur.fetchall()

    if empty_orders:
        raise RuntimeError(
            f"Có sales_order không có order_line hợp lệ trong staging, ví dụ: {empty_orders}"
        )


# ============================================================
# IMPORT TRANSACTION DATA
# ============================================================

def import_sales_order(cur) -> int:
    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.sales_order (
            so_number,
            invoice_symbol,
            invoice_number,
            order_date,
            customer_code
        )
        SELECT
            so_number,
            invoice_symbol,
            invoice_number,
            order_date,
            customer_code
        FROM tmp_sales_order
        ON CONFLICT (so_number) DO UPDATE
        SET
            invoice_symbol = EXCLUDED.invoice_symbol,
            invoice_number = EXCLUDED.invoice_number,
            order_date = EXCLUDED.order_date,
            customer_code = EXCLUDED.customer_code;
        """
    )

    return cur.rowcount


def delete_existing_order_lines_for_batch(cur) -> int:
    cur.execute(
        f"""
        DELETE FROM {DB_SCHEMA}.order_line ol
        USING tmp_sales_order tso
        WHERE ol.so_number = tso.so_number;
        """
    )

    return cur.rowcount


def import_order_line(cur) -> int:
    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.order_line (
            order_id,
            so_number,
            product_code,
            quantity,
            unit_price,
            line_total
        )
        SELECT
            so.order_id,
            tol.so_number,
            tol.product_code,
            tol.quantity,
            tol.unit_price,
            tol.line_total
        FROM tmp_order_line tol
        JOIN {DB_SCHEMA}.sales_order so
            ON so.so_number = tol.so_number;
        """
    )

    return cur.rowcount


# ============================================================
# VERIFY
# ============================================================

def verify_import(cur) -> dict:
    cur.execute("SELECT COUNT(*) FROM tmp_sales_order;")
    staging_orders = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM tmp_order_line;")
    staging_lines = cur.fetchone()[0]

    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM {DB_SCHEMA}.sales_order so
        JOIN tmp_sales_order tso
            ON tso.so_number = so.so_number;
        """
    )
    imported_orders = cur.fetchone()[0]

    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM {DB_SCHEMA}.order_line ol
        JOIN tmp_sales_order tso
            ON tso.so_number = ol.so_number;
        """
    )
    imported_lines_for_batch = cur.fetchone()[0]

    cur.execute(
        f"""
        SELECT
            COALESCE(SUM(ol.line_total), 0),
            COALESCE(SUM(ol.quantity), 0),
            COUNT(*)
        FROM {DB_SCHEMA}.order_line ol
        JOIN tmp_sales_order tso
            ON tso.so_number = ol.so_number;
        """
    )
    batch_total_amount, batch_total_quantity, batch_line_count = cur.fetchone()

    return {
        "staging_orders": staging_orders,
        "staging_lines": staging_lines,
        "imported_orders": imported_orders,
        "imported_lines_for_batch": imported_lines_for_batch,
        "batch_total_amount": batch_total_amount,
        "batch_total_quantity": batch_total_quantity,
        "batch_line_count": batch_line_count,
    }


# ============================================================
# MAIN SERVICE
# ============================================================

def load_staging_to_db(
    staging_dir: str | Path = DEFAULT_STAGING_DIR,
    dry_run: bool = False,
) -> dict:
    """
    Load toàn bộ staging CSV vào DB.

    dry_run=True:
        vẫn chạy toàn bộ validate/import trong transaction
        nhưng rollback cuối cùng để test.
    """

    paths = get_staging_paths(staging_dir)

    logger.info("=" * 80)
    logger.info("LOAD STAGING TO DB STARTED")
    logger.info("=" * 80)
    logger.info("Staging dir : %s", resolve_project_path(staging_dir))
    logger.info("Schema      : %s", DB_SCHEMA)
    logger.info("Dry run     : %s", dry_run)
    logger.info("=" * 80)

    email_rows = load_email_log_rows(paths["email_log"])
    staging_customer_rows = load_staging_customer_rows(paths["customer"])
    sales_order_rows = load_sales_order_rows(paths["sales_order"])
    order_line_rows = load_order_line_rows(paths["order_line"])

    if sales_order_rows and not order_line_rows:
        raise RuntimeError("Có staging_sales_order nhưng không có staging_order_line")

    if order_line_rows and not sales_order_rows:
        raise RuntimeError("Có staging_order_line nhưng không có staging_sales_order")

    summary = {
        "email_log_imported": 0,
        "customers_upserted": 0,
        "temp_sales_orders": 0,
        "temp_order_lines": 0,
        "sales_orders_upserted": 0,
        "old_order_lines_deleted": 0,
        "order_lines_inserted": 0,
        "dry_run": dry_run,
        "verify": {},
    }

    conn = None

    try:
        with get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET search_path TO {DB_SCHEMA}, public;")

                    ensure_email_log_table(cur)

                    create_temp_tables(cur)

                    summary["temp_sales_orders"] = bulk_insert_temp_sales_order(
                        cur,
                        sales_order_rows,
                    )

                    summary["temp_order_lines"] = bulk_insert_temp_order_line(
                        cur,
                        order_line_rows,
                    )

                    summary["email_log_imported"] = import_email_log(
                        cur,
                        email_rows,
                    )

                    summary["customers_upserted"] = import_staging_customer(
                        cur,
                        staging_customer_rows,
                    )

                    if sales_order_rows and order_line_rows:
                        validate_temp_data(cur)

                        summary["sales_orders_upserted"] = import_sales_order(cur)
                        summary["old_order_lines_deleted"] = delete_existing_order_lines_for_batch(cur)
                        summary["order_lines_inserted"] = import_order_line(cur)
                        summary["verify"] = verify_import(cur)

                    else:
                        logger.warning(
                            "No sales_order/order_line rows to import. Only logs/customers were loaded."
                        )

                        summary["verify"] = {
                            "staging_orders": 0,
                            "staging_lines": 0,
                            "imported_orders": 0,
                            "imported_lines_for_batch": 0,
                            "batch_total_amount": 0,
                            "batch_total_quantity": 0,
                            "batch_line_count": 0,
                        }

                if dry_run:
                    conn.rollback()
                    logger.warning("DRY RUN: transaction rolled back")
                else:
                    conn.commit()
                    logger.info("Transaction committed")

            except Exception:
                conn.rollback()
                logger.exception("LOAD STAGING TO DB FAILED")
                raise

    except Exception:
        raise

    logger.info("=" * 80)
    logger.info("LOAD STAGING TO DB FINISHED")
    logger.info("Email log imported      : %s", summary["email_log_imported"])
    logger.info("Customers upserted      : %s", summary["customers_upserted"])
    logger.info("Sales orders upserted   : %s", summary["sales_orders_upserted"])
    logger.info("Old order lines deleted : %s", summary["old_order_lines_deleted"])
    logger.info("Order lines inserted    : %s", summary["order_lines_inserted"])
    logger.info("Dry run                 : %s", summary["dry_run"])
    logger.info("Verify                  : %s", summary["verify"])
    logger.info("=" * 80)

    return summary


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load TNBIKE staging CSV files to PostgreSQL"
    )

    parser.add_argument(
        "--staging-dir",
        default=DEFAULT_STAGING_DIR,
        help="Folder containing staging CSV files",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run import in transaction then rollback",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="load_staging_to_db.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        summary = load_staging_to_db(
            staging_dir=args.staging_dir,
            dry_run=args.dry_run,
        )

        verify = summary.get("verify", {})

        print("")
        print("LOAD STAGING TO DB SUCCESS")
        print(f"Email log imported       : {summary['email_log_imported']}")
        print(f"Customers upserted       : {summary['customers_upserted']}")
        print(f"Temp sales orders        : {summary['temp_sales_orders']}")
        print(f"Temp order lines         : {summary['temp_order_lines']}")
        print(f"Sales orders upserted    : {summary['sales_orders_upserted']}")
        print(f"Old order lines deleted  : {summary['old_order_lines_deleted']}")
        print(f"Order lines inserted     : {summary['order_lines_inserted']}")
        print(f"Dry run                  : {summary['dry_run']}")

        if verify:
            print(f"Verify staging orders    : {verify['staging_orders']}")
            print(f"Verify staging lines     : {verify['staging_lines']}")
            print(f"Verify imported orders   : {verify['imported_orders']}")
            print(f"Verify imported lines    : {verify['imported_lines_for_batch']}")
            print(f"Batch total amount       : {verify['batch_total_amount']}")
            print(f"Batch total quantity     : {verify['batch_total_quantity']}")
            print(f"Batch line count         : {verify['batch_line_count']}")

    except Exception as e:
        logger.exception("LOAD STAGING TO DB FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
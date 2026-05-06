from pathlib import Path
import csv
import os

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values


load_dotenv()


STAGING_DIR = Path("data/staging")

EMAIL_LOG_CSV = STAGING_DIR / "staging_email_log.csv"
SALES_ORDER_CSV = STAGING_DIR / "staging_sales_order.csv"
ORDER_LINE_CSV = STAGING_DIR / "staging_order_line.csv"


# ============================================================
# DB config
# ============================================================

def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Thiếu biến môi trường: {name}. "
            f"Hãy kiểm tra file .env ở root project."
        )

    return value


DB_CONFIG = {
    "host": get_required_env("PGHOST"),
    "port": os.getenv("PGPORT", "5432"),
    "database": get_required_env("PGDATABASE"),
    "user": get_required_env("PGUSER"),
    "password": get_required_env("PGPASSWORD"),
}


# ============================================================
# Helpers
# ============================================================

def clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def empty_to_none(value):
    value = clean_text(value)
    return value if value else None


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {path}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def require_columns(path: Path, rows: list[dict], required_columns: list[str]) -> None:
    if not rows:
        return

    actual = set(rows[0].keys())
    missing = [col for col in required_columns if col not in actual]

    if missing:
        raise RuntimeError(
            f"File {path} thiếu cột: {missing}. "
            f"Cột hiện có: {sorted(actual)}"
        )


# ============================================================
# Prepare rows
# ============================================================

def load_email_log_rows() -> list[tuple]:
    rows = read_csv_rows(EMAIL_LOG_CSV)

    require_columns(
        EMAIL_LOG_CSV,
        rows,
        [
            "message_id",
            "from_address",
            "received_at",
            "attachment_name",
            "processing_status",
        ],
    )

    output = []

    for row in rows:
        output.append(
            (
                empty_to_none(row.get("message_id")),
                empty_to_none(row.get("from_address")),
                empty_to_none(row.get("received_at")),
                empty_to_none(row.get("attachment_name")),
                empty_to_none(row.get("processing_status")),
            )
        )

    return output


def load_sales_order_rows() -> list[tuple]:
    rows = read_csv_rows(SALES_ORDER_CSV)

    require_columns(
        SALES_ORDER_CSV,
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

    for row in rows:
        so_number = empty_to_none(row.get("so_number"))
        order_date = empty_to_none(row.get("order_date"))
        customer_code = empty_to_none(row.get("customer_code"))

        if not so_number or not order_date or not customer_code:
            raise RuntimeError(
                f"sales_order có dòng thiếu NOT NULL: "
                f"so_number={so_number}, order_date={order_date}, customer_code={customer_code}"
            )

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


def load_order_line_rows() -> list[tuple]:
    rows = read_csv_rows(ORDER_LINE_CSV)

    require_columns(
        ORDER_LINE_CSV,
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
        quantity = empty_to_none(row.get("quantity"))
        unit_price = empty_to_none(row.get("unit_price"))
        line_total = empty_to_none(row.get("line_total"))

        if not so_number or not product_code or not quantity or not unit_price or not line_total:
            raise RuntimeError(
                f"order_line có dòng thiếu NOT NULL: "
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
# DDL for email_log
# ============================================================

def ensure_email_log_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tnbike.email_log (
            email_log_id        BIGSERIAL PRIMARY KEY,
            message_id          TEXT UNIQUE,
            from_address        TEXT,
            received_at         TIMESTAMPTZ,
            attachment_name     TEXT,
            processing_status   TEXT,
            created_at          TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )


# ============================================================
# Import logic
# ============================================================

def import_email_log(cur, rows: list[tuple]) -> int:
    if not rows:
        return 0

    execute_values(
        cur,
        """
        INSERT INTO tnbike.email_log (
            message_id,
            from_address,
            received_at,
            attachment_name,
            processing_status
        )
        VALUES %s
        ON CONFLICT (message_id) DO UPDATE
        SET
            from_address = EXCLUDED.from_address,
            received_at = EXCLUDED.received_at,
            attachment_name = EXCLUDED.attachment_name,
            processing_status = EXCLUDED.processing_status;
        """,
        rows,
    )

    return len(rows)


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


def bulk_insert_temp_sales_order(cur, rows: list[tuple]) -> None:
    if not rows:
        return

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


def bulk_insert_temp_order_line(cur, rows: list[tuple]) -> None:
    if not rows:
        return

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


def validate_temp_data(cur) -> None:
    """
    Check lại FK trước khi insert thật.
    Nếu còn lỗi thì dừng import.
    """

    cur.execute(
        """
        SELECT tso.customer_code
        FROM tmp_sales_order tso
        LEFT JOIN tnbike.customer c
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
        """
        SELECT tol.product_code
        FROM tmp_order_line tol
        LEFT JOIN tnbike.product p
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


def import_sales_order(cur) -> int:
    """
    Insert/upsert sales_order.
    Không insert:
    - order_id
    - total_amount
    - total_quantity
    - line_count
    - fiscal_year/month/quarter
    - created_at
    """

    cur.execute(
        """
        INSERT INTO tnbike.sales_order (
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
    """
    Để import idempotent:
    xóa toàn bộ order_line của các so_number trong batch rồi insert lại.
    Trigger sẽ tự cập nhật total_amount, total_quantity, line_count.
    """

    cur.execute(
        """
        DELETE FROM tnbike.order_line ol
        USING tmp_sales_order tso
        WHERE ol.so_number = tso.so_number;
        """
    )

    return cur.rowcount


def import_order_line(cur) -> int:
    """
    Insert order_line.
    order_id được lookup từ tnbike.sales_order bằng so_number.
    """

    cur.execute(
        """
        INSERT INTO tnbike.order_line (
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
        JOIN tnbike.sales_order so
            ON so.so_number = tol.so_number;
        """
    )

    return cur.rowcount


def verify_import(cur) -> dict:
    """
    Verify theo batch so_number, không join trực tiếp tmp_order_line với order_line
    theo product/quantity/price vì nếu có dòng trùng hoàn toàn sẽ bị many-to-many
    và COUNT(*) bị lớn hơn thực tế.
    """

    cur.execute("SELECT COUNT(*) FROM tmp_sales_order;")
    staging_orders = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM tmp_order_line;")
    staging_lines = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM tnbike.sales_order so
        JOIN tmp_sales_order tso
            ON tso.so_number = so.so_number;
        """
    )
    imported_orders = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM tnbike.order_line ol
        JOIN tmp_sales_order tso
            ON tso.so_number = ol.so_number;
        """
    )
    imported_lines_for_batch = cur.fetchone()[0]

    cur.execute(
        """
        SELECT
            COALESCE(SUM(ol.line_total), 0),
            COALESCE(SUM(ol.quantity), 0),
            COUNT(*)
        FROM tnbike.order_line ol
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
# Main
# ============================================================

def main():
    email_rows = load_email_log_rows()
    sales_order_rows = load_sales_order_rows()
    order_line_rows = load_order_line_rows()

    if not sales_order_rows:
        raise RuntimeError(f"Không có dòng nào trong {SALES_ORDER_CSV}")

    if not order_line_rows:
        raise RuntimeError(f"Không có dòng nào trong {ORDER_LINE_CSV}")

    with psycopg2.connect(**DB_CONFIG) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO tnbike, public;")

                ensure_email_log_table(cur)

                create_temp_tables(cur)
                bulk_insert_temp_sales_order(cur, sales_order_rows)
                bulk_insert_temp_order_line(cur, order_line_rows)

                validate_temp_data(cur)

                email_count = import_email_log(cur, email_rows)
                sales_order_count = import_sales_order(cur)
                deleted_line_count = delete_existing_order_lines_for_batch(cur)
                order_line_count = import_order_line(cur)

                stats = verify_import(cur)

            conn.commit()

        except Exception:
            conn.rollback()
            raise

    print(f"Email log imported        : {email_count}")
    print(f"Sales orders upserted     : {sales_order_count}")
    print(f"Old order lines deleted   : {deleted_line_count}")
    print(f"Order lines inserted      : {order_line_count}")
    print(f"Verify staging orders     : {stats['staging_orders']}")
    print(f"Verify staging lines      : {stats['staging_lines']}")
    print(f"Verify imported orders    : {stats['imported_orders']}")
    print(f"Verify imported lines     : {stats['imported_lines_for_batch']}")
    print(f"Batch total amount        : {stats['batch_total_amount']}")
    print(f"Batch total quantity      : {stats['batch_total_quantity']}")
    print(f"Batch line count          : {stats['batch_line_count']}")


if __name__ == "__main__":
    main()
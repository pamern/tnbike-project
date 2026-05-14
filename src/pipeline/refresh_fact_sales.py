from pathlib import Path
import csv
import os

from dotenv import load_dotenv
import psycopg2


load_dotenv()

STAGING_SALES_ORDER_CSV = Path("data/staging/staging_sales_order.csv")


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Thiếu biến môi trường: {name}. Hãy kiểm tra file .env."
        )

    return value


DB_CONFIG = {
    "host": get_required_env("PGHOST"),
    "port": os.getenv("PGPORT", "5432"),
    "database": get_required_env("PGDATABASE"),
    "user": get_required_env("PGUSER"),
    "password": get_required_env("PGPASSWORD"),
}


def read_so_numbers_from_staging() -> list[str]:
    if not STAGING_SALES_ORDER_CSV.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {STAGING_SALES_ORDER_CSV}")

    so_numbers = []

    with open(STAGING_SALES_ORDER_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if "so_number" not in reader.fieldnames:
            raise RuntimeError(
                f"File {STAGING_SALES_ORDER_CSV} thiếu cột so_number"
            )

        for row in reader:
            so_number = str(row.get("so_number", "")).strip()
            if so_number:
                so_numbers.append(so_number)

    return sorted(set(so_numbers))


def refresh_fact_sales_for_so_numbers(cur, so_numbers: list[str]) -> dict:
    if not so_numbers:
        return {
            "deleted": 0,
            "inserted": 0,
        }

    cur.execute(
        """
        DELETE FROM tnbike.fact_sales
        WHERE so_number = ANY(%s);
        """,
        (so_numbers,),
    )
    deleted_count = cur.rowcount

    cur.execute(
        """
        INSERT INTO tnbike.fact_sales (
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
            so.fiscal_year,
            so.fiscal_quarter,
            so.fiscal_month,
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
        FROM tnbike.order_line ol
        JOIN tnbike.sales_order so
            ON so.order_id = ol.order_id
        JOIN tnbike.customer c
            ON c.customer_code = so.customer_code
        LEFT JOIN tnbike.province p
            ON p.province_id = c.province_id
        JOIN tnbike.product pr
            ON pr.product_code = ol.product_code
        LEFT JOIN tnbike.product_line pl
            ON pl.line_id = pr.line_id
        LEFT JOIN tnbike.product_group pg
            ON pg.group_code = pl.group_code
        WHERE so.so_number = ANY(%s);
        """,
        (so_numbers,),
    )
    inserted_count = cur.rowcount

    return {
        "deleted": deleted_count,
        "inserted": inserted_count,
    }


def main():
    so_numbers = read_so_numbers_from_staging()

    if not so_numbers:
        print("Không có so_number nào cần refresh fact_sales.")
        return

    with psycopg2.connect(**DB_CONFIG) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO tnbike, public;")
                stats = refresh_fact_sales_for_so_numbers(cur, so_numbers)

            conn.commit()

        except Exception:
            conn.rollback()
            raise

    print("Refresh fact_sales completed")
    print(f"SO numbers : {len(so_numbers)}")
    print(f"Deleted    : {stats['deleted']}")
    print(f"Inserted   : {stats['inserted']}")


if __name__ == "__main__":
    main()
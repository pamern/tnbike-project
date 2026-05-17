import psycopg2
import pandas as pd
from dotenv import load_dotenv
import os
import logging

# ============================================================
# CONFIG LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)

# ============================================================
# LOAD ENV
# ============================================================
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "tnbike_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

CSV_PATH = "data/processed/cleaned/success_mapping_customer_province.csv"

conn = None
cur = None

try:
    logger.info("Bắt đầu cập nhật province_id, province_name, region vào database")
    logger.info(f"Kết nối DB: host={DB_HOST}, port={DB_PORT}, db={DB_NAME}, user={DB_USER}")

    # ============================================================
    # CONNECT DATABASE
    # ============================================================
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

    cur = conn.cursor()

    cur.execute("SET search_path TO tnbike, public;")
    logger.info("Đã set search_path = tnbike, public")

    # ============================================================
    # READ CSV
    # ============================================================
    logger.info(f"Đang đọc file CSV: {CSV_PATH}")

    success_df = pd.read_csv(
        CSV_PATH,
        dtype={
            "customer_code": str,
            "province_id": "Int64",
            "province_name_extract": str
        }
    )

    logger.info(f"Số dòng đọc được từ CSV: {len(success_df):,}")
    logger.info(f"Các cột trong CSV: {list(success_df.columns)}")

    # ============================================================
    # VALIDATE REQUIRED COLUMNS
    # ============================================================
    required_cols = ["customer_code", "province_id", "province_name_extract"]

    missing_cols = [col for col in required_cols if col not in success_df.columns]

    if missing_cols:
        raise ValueError(f"CSV thiếu các cột bắt buộc: {missing_cols}")

    # ============================================================
    # CLEAN DATA
    # ============================================================
    success_df["customer_code"] = success_df["customer_code"].astype(str).str.strip()

    success_df = success_df.dropna(subset=["customer_code", "province_id"])

    success_df["province_id"] = success_df["province_id"].astype(int)

    # Loại duplicate customer_code để tránh update lặp
    before_dedup = len(success_df)

    success_df = success_df.drop_duplicates(
        subset=["customer_code"],
        keep="last"
    )

    after_dedup = len(success_df)

    logger.info(f"Số dòng sau khi bỏ duplicate customer_code: {after_dedup:,}")
    logger.info(f"Số dòng duplicate đã loại bỏ: {before_dedup - after_dedup:,}")

    # ============================================================
    # UPDATE CUSTOMER
    # ============================================================
    logger.info("Bắt đầu cập nhật bảng customer...")

    updated_customer_count = 0
    not_found_customers = []

    for index, row in success_df.iterrows():
        customer_code = row["customer_code"]
        province_id = int(row["province_id"])

        cur.execute("""
            UPDATE customer
            SET province_id = %s,
                updated_at = NOW()
            WHERE customer_code = %s;
        """, (province_id, customer_code))

        affected = cur.rowcount
        updated_customer_count += affected

        if affected == 0:
            not_found_customers.append(customer_code)

        if (updated_customer_count > 0) and (updated_customer_count % 100 == 0):
            logger.info(f"Đã update customer affected rows: {updated_customer_count:,}")

    logger.info(f"Hoàn tất cập nhật customer. Số dòng bị ảnh hưởng: {updated_customer_count:,}")

    if not_found_customers:
        logger.warning(f"Số customer_code không tìm thấy trong DB: {len(not_found_customers):,}")
        logger.warning(f"Ví dụ customer_code không tìm thấy: {not_found_customers[:10]}")

    # ============================================================
    # UPDATE FACT_SALES
    # ============================================================
    logger.info("Bắt đầu cập nhật bảng fact_sales...")

    cur.execute("""
        UPDATE fact_sales fs
        SET 
            province_id = p.province_id,
            province_name = p.province_name,
            region = p.region
        FROM customer c
        JOIN province p
            ON c.province_id = p.province_id
        WHERE fs.customer_code = c.customer_code;
    """)

    updated_fact_sales_count = cur.rowcount

    logger.info(f"Hoàn tất cập nhật fact_sales. Số dòng bị ảnh hưởng: {updated_fact_sales_count:,}")

    # ============================================================
    # CHECK RESULT
    # ============================================================
    logger.info("Kiểm tra kết quả sau cập nhật...")

    cur.execute("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(province_id) AS rows_has_province_id,
            COUNT(province_name) AS rows_has_province_name,
            COUNT(region) AS rows_has_region
        FROM fact_sales;
    """)

    check_result = cur.fetchone()

    total_rows = check_result[0]
    rows_has_province_id = check_result[1]
    rows_has_province_name = check_result[2]
    rows_has_region = check_result[3]

    logger.info("========== KIỂM TRA FACT_SALES ==========")
    logger.info(f"Tổng dòng fact_sales        : {total_rows:,}")
    logger.info(f"Dòng có province_id         : {rows_has_province_id:,}")
    logger.info(f"Dòng có province_name       : {rows_has_province_name:,}")
    logger.info(f"Dòng có region              : {rows_has_region:,}")
    logger.info(f"Dòng thiếu province_id      : {total_rows - rows_has_province_id:,}")
    logger.info(f"Dòng thiếu province_name    : {total_rows - rows_has_province_name:,}")
    logger.info(f"Dòng thiếu region           : {total_rows - rows_has_region:,}")

    # ============================================================
    # CHECK MISSING REGION DETAIL
    # ============================================================
    cur.execute("""
        SELECT 
            fs.customer_code,
            fs.customer_name,
            fs.province_id,
            fs.province_name,
            fs.region,
            COUNT(*) AS rows_count
        FROM fact_sales fs
        WHERE fs.province_id IS NULL
           OR fs.province_name IS NULL
           OR fs.region IS NULL
        GROUP BY
            fs.customer_code,
            fs.customer_name,
            fs.province_id,
            fs.province_name,
            fs.region
        ORDER BY rows_count DESC
        LIMIT 20;
    """)

    missing_rows = cur.fetchall()

    if missing_rows:
        logger.warning("Vẫn còn dòng fact_sales thiếu province/region. Top 20:")
        for row in missing_rows:
            logger.warning(row)
    else:
        logger.info("Không còn dòng fact_sales thiếu province_id/province_name/region")

    # ============================================================
    # COMMIT
    # ============================================================
    conn.commit()

    logger.info("Đã commit thay đổi vào database")

    logger.info("========== KẾT QUẢ CUỐI ==========")
    logger.info(f"Tổng dòng CSV sau làm sạch       : {len(success_df):,}")
    logger.info(f"Customer rows affected           : {updated_customer_count:,}")
    logger.info(f"Fact_sales rows affected         : {updated_fact_sales_count:,}")
    logger.info(f"Customer không tìm thấy trong DB : {len(not_found_customers):,}")
    logger.info("Hoàn thành cập nhật province_id, province_name, region")

except Exception as e:
    if conn is not None:
        conn.rollback()
        logger.warning("Đã rollback do có lỗi")

    logger.exception(f"Lỗi trong quá trình cập nhật DB: {e}")

finally:
    if cur is not None:
        cur.close()
        logger.info("Đã đóng cursor")

    if conn is not None:
        conn.close()
        logger.info("Đã đóng kết nối database")
import logging
import psycopg2
import pandas as pd
from dotenv import load_dotenv
import os
import sys
from pathlib import Path

# ============================================================
# CONFIG LOGGING: ghi file + in console
# ============================================================
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

file_handler = logging.FileHandler("update_log.log", encoding="utf-8")
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# ============================================================
# LOAD ENV
# ============================================================
load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'tnbike_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

CSV_PATH = Path(r"data/processed/cleaned/product_cleaned.csv")

conn = None
cur = None


def clean_color(value):
    """
    Convert các giá trị rỗng/NaN thành None để PostgreSQL lưu NULL.
    """
    if pd.isna(value):
        return None

    value = str(value).strip()

    if value == "":
        return None

    if value.lower() in ["nan", "none", "null", "na", "n/a"]:
        return None

    return value


try:
    logger.info("Bắt đầu cập nhật color từ product_cleaned.csv vào database")
    logger.info(f"Kết nối DB: host={DB_HOST}, port={DB_PORT}, db={DB_NAME}, user={DB_USER}")

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Không tìm thấy file CSV: {CSV_PATH}")

    # ========================================================
    # CONNECT DB
    # ========================================================
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

    # ========================================================
    # LOAD CSV
    # ========================================================
    logger.info(f"Đang đọc file CSV: {CSV_PATH}")

    df_cleaned = pd.read_csv(
        CSV_PATH,
        dtype={
            "product_code": str,
            "color_new": str
        },
        keep_default_na=True
    )

    logger.info(f"Số dòng đọc được từ CSV: {len(df_cleaned):,}")
    logger.info(f"Các cột trong CSV: {list(df_cleaned.columns)}")

    required_cols = {"product_code", "color_new"}
    missing_cols = required_cols - set(df_cleaned.columns)

    if missing_cols:
        raise ValueError(f"File CSV thiếu cột bắt buộc: {missing_cols}")

    # ========================================================
    # CLEAN DATA
    # ========================================================
    logger.info("Đang làm sạch product_code và color_new...")

    df_cleaned["product_code"] = df_cleaned["product_code"].astype(str).str.strip()
    df_cleaned["color_new"] = df_cleaned["color_new"].apply(clean_color)

    # Bỏ dòng không có product_code
    before_drop = len(df_cleaned)
    df_cleaned = df_cleaned[
        df_cleaned["product_code"].notna()
        & (df_cleaned["product_code"].str.strip() != "")
        & (df_cleaned["product_code"].str.lower() != "nan")
    ].copy()

    after_drop = len(df_cleaned)

    logger.info(f"Số dòng bị bỏ do thiếu product_code: {before_drop - after_drop:,}")
    logger.info(f"Số dòng hợp lệ để update: {after_drop:,}")

    null_color_count = df_cleaned["color_new"].isna().sum()
    non_null_color_count = after_drop - null_color_count

    logger.info("========== THỐNG KÊ COLOR ==========")
    logger.info(f"Số dòng color_new có giá trị     : {non_null_color_count:,}")
    logger.info(f"Số dòng color_new NULL/NaN/rỗng  : {null_color_count:,}")

    # Preview vài dòng color NULL
    if null_color_count > 0:
        logger.warning("Có color_new bị rỗng/NaN. Các dòng này sẽ được SET NULL trong product và fact_sales.")
        preview_null = df_cleaned[df_cleaned["color_new"].isna()][["product_code"]].head(10)

        for _, row in preview_null.iterrows():
            logger.warning(f"product_code SET NULL color: {row['product_code']}")

    # ========================================================
    # PREPARE UPDATE DATA
    # ========================================================
    update_data = []

    for _, row in df_cleaned.iterrows():
        product_code = row["product_code"]
        color_new = row["color_new"]

        # color_new là None thì PostgreSQL sẽ lưu thành NULL
        update_data.append((color_new, product_code))

    logger.info(f"Đã chuẩn bị {len(update_data):,} dòng update.")

    # ========================================================
    # UPDATE PRODUCT
    # ========================================================
    logger.info("Đang cập nhật bảng product.color...")

    update_product_query = """
        UPDATE product
        SET color = %s
        WHERE product_code = %s;
    """

    cur.executemany(update_product_query, update_data)
    product_rows_updated = cur.rowcount

    logger.info(f"Số dòng product bị ảnh hưởng: {product_rows_updated:,}")

    # ========================================================
    # UPDATE FACT_SALES
    # ========================================================
    logger.info("Đang cập nhật bảng fact_sales.color...")

    update_fact_sales_query = """
        UPDATE fact_sales
        SET color = %s
        WHERE product_code = %s;
    """

    cur.executemany(update_fact_sales_query, update_data)
    fact_sales_rows_updated = cur.rowcount

    logger.info(f"Số dòng fact_sales bị ảnh hưởng: {fact_sales_rows_updated:,}")

    # ========================================================
    # CHECK SAU UPDATE
    # ========================================================
    logger.info("Đang kiểm tra lại dữ liệu sau update...")

    cur.execute("""
        SELECT COUNT(*)
        FROM product
        WHERE color IS NULL;
    """)
    product_null_color = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM fact_sales
        WHERE color IS NULL;
    """)
    fact_sales_null_color = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM product
        WHERE LOWER(color) IN ('nan', 'none', 'null');
    """)
    product_dirty_color = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM fact_sales
        WHERE LOWER(color) IN ('nan', 'none', 'null');
    """)
    fact_sales_dirty_color = cur.fetchone()[0]

    logger.info("========== CHECK SAU UPDATE ==========")
    logger.info(f"product.color IS NULL              : {product_null_color:,}")
    logger.info(f"fact_sales.color IS NULL           : {fact_sales_null_color:,}")
    logger.info(f"product.color dạng text bẩn         : {product_dirty_color:,}")
    logger.info(f"fact_sales.color dạng text bẩn      : {fact_sales_dirty_color:,}")

    # ========================================================
    # COMMIT
    # ========================================================
    conn.commit()

    logger.info("Đã commit thay đổi vào database.")
    logger.info("========== KẾT QUẢ CUỐI ==========")
    logger.info(f"CSV rows xử lý              : {len(df_cleaned):,}")
    logger.info(f"Product rows affected       : {product_rows_updated:,}")
    logger.info(f"Fact_sales rows affected    : {fact_sales_rows_updated:,}")
    logger.info("Color update completed successfully.")

except Exception as e:
    if conn:
        conn.rollback()
        logger.warning("Đã rollback do có lỗi.")

    logger.exception(f"Lỗi trong quá trình cập nhật color: {e}")

finally:
    if cur:
        cur.close()
        logger.info("Đã đóng cursor.")

    if conn:
        conn.close()
        logger.info("Đã đóng kết nối database.")
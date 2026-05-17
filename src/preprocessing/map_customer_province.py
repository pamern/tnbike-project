import psycopg2
import pandas as pd
from unidecode import unidecode
from dotenv import load_dotenv
import os
import logging
from pathlib import Path

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

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'tnbike_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

OUTPUT_DIR = Path("data/processed/cleaned")
SUCCESS_FILE = OUTPUT_DIR / "success_mapping_customer_province.csv"
FAILED_FILE = OUTPUT_DIR / "failed_mapping_customer_province.csv"

# ============================================================
# REPLACEMENT DICT
# ============================================================
replacement_dict = {
    'TP Hồ Chí Minh': 'TP. Hồ Chí Minh',
    'Thành phố Hồ Chí Minh': 'TP. Hồ Chí Minh',
    'Hà Nộ': 'Hà Nội',
    'Nghệ A': 'Nghệ An',
    'Hải Dươn': 'Hải Dương',
    'TP Huế': 'Thừa Thiên Huế'
}

# ============================================================
# FUNCTIONS
# ============================================================
def normalize_text(text):
    if pd.isna(text):
        return ""
    return unidecode(str(text).lower()).strip()


def extract_province_from_address(address, province_list):
    if pd.isna(address) or str(address).strip() == "":
        return None

    address = str(address)

    # Sửa lỗi chính tả phổ biến
    for wrong, correct in replacement_dict.items():
        address = address.replace(wrong, correct)

    address_clean = normalize_text(address)

    best_match = None

    for _, row in province_list.iterrows():
        province_name_clean = normalize_text(row['province_name'])

        if province_name_clean in address_clean:
            best_match = row['province_name']
            break

    return best_match


# ============================================================
# MAIN
# ============================================================
conn = None
cur = None

try:
    logger.info("Bắt đầu mapping customer -> province")
    logger.info(f"Kết nối DB: host={DB_HOST}, port={DB_PORT}, db={DB_NAME}, user={DB_USER}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Đã kiểm tra/tạo thư mục output: {OUTPUT_DIR}")

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

    logger.info("Đang đọc bảng province...")
    cur.execute("SELECT province_id, province_name FROM province")
    province_data = cur.fetchall()

    logger.info("Đang đọc bảng customer có address khác NULL...")
    cur.execute("""
        SELECT customer_code, customer_name, address
        FROM customer
        WHERE address IS NOT NULL
    """)
    customer_data = cur.fetchall()

    provinces_df = pd.DataFrame(
        province_data,
        columns=['province_id', 'province_name']
    )

    customers_df = pd.DataFrame(
        customer_data,
        columns=['customer_code', 'customer_name', 'address']
    )

    logger.info(f"Số tỉnh/thành đọc được: {len(provinces_df):,}")
    logger.info(f"Số customer có address: {len(customers_df):,}")

    if provinces_df.empty:
        logger.warning("Bảng province không có dữ liệu. Dừng xử lý.")
        raise ValueError("province table is empty")

    if customers_df.empty:
        logger.warning("Không có customer nào có address. Dừng xử lý.")
        raise ValueError("customer address data is empty")

    logger.info("Đang extract province từ address...")
    customers_df['province_name_extract'] = customers_df['address'].apply(
        lambda x: extract_province_from_address(x, provinces_df)
    )

    logger.info("Đang merge province_id theo province_name_extract...")
    customers_df = customers_df.merge(
        provinces_df,
        left_on='province_name_extract',
        right_on='province_name',
        how='left'
    )

    success_df = customers_df[customers_df['province_id'].notnull()].copy()
    failed_df = customers_df[customers_df['province_id'].isnull()].copy()

    total = len(customers_df)
    success_count = len(success_df)
    failed_count = len(failed_df)
    success_rate = success_count / total * 100 if total > 0 else 0

    logger.info("========== KẾT QUẢ MAPPING ==========")
    logger.info(f"Tổng customer xử lý     : {total:,}")
    logger.info(f"Mapping thành công      : {success_count:,}")
    logger.info(f"Mapping thất bại        : {failed_count:,}")
    logger.info(f"Tỷ lệ thành công        : {success_rate:.2f}%")

    logger.info(f"Đang xuất file success: {SUCCESS_FILE}")
    success_df.to_csv(SUCCESS_FILE, index=False, encoding="utf-8-sig")

    logger.info(f"Đang xuất file failed: {FAILED_FILE}")
    failed_df.to_csv(FAILED_FILE, index=False, encoding="utf-8-sig")

    logger.info("Xuất file hoàn tất.")

    if failed_count > 0:
        logger.warning("Có customer chưa map được province. Kiểm tra file failed_mapping_customer_province.csv")
        logger.warning("Ví dụ một số dòng failed:")
        preview_failed = failed_df[['customer_code', 'customer_name', 'address']].head(5)

        for _, row in preview_failed.iterrows():
            logger.warning(
                f"{row['customer_code']} | {row['customer_name']} | {row['address']}"
            )

    logger.info("Hoàn thành mapping customer province.")

except Exception as e:
    logger.exception(f"Lỗi trong quá trình mapping: {e}")

finally:
    if cur is not None:
        cur.close()
        logger.info("Đã đóng cursor.")

    if conn is not None:
        conn.close()
        logger.info("Đã đóng kết nối database.")
import psycopg2
import pandas as pd
from unidecode import unidecode
from dotenv import load_dotenv
import os

# Tải thông tin từ file .env
load_dotenv()

# Lấy thông tin cấu hình từ môi trường
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'tnbike_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

# Kết nối đến PostgreSQL
conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, database=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)

# Tạo cursor
cur = conn.cursor()

# Đặt schema search_path để đảm bảo các truy vấn chạy trong schema tnbike
cur.execute("SET search_path TO tnbike, public;")

# Truy vấn bảng province để lấy các tỉnh
cur.execute("SELECT province_id, province_name FROM province")
province_data = cur.fetchall()

# Truy vấn bảng customer để lấy thông tin địa chỉ khách hàng
cur.execute("SELECT customer_code, customer_name, address FROM customer WHERE address IS NOT NULL")
customer_data = cur.fetchall()

# Chuyển dữ liệu thành DataFrame
provinces_df = pd.DataFrame(province_data, columns=['province_id', 'province_name'])
customers_df = pd.DataFrame(customer_data, columns=['customer_code', 'customer_name', 'address'])

# Tạo từ điển thay thế các sai sót chính tả phổ biến
replacement_dict = {
    'TP Hồ Chí Minh': 'TP. Hồ Chí Minh',
    'Thành phố Hồ Chí Minh': 'TP. Hồ Chí Minh',
    'Hà Nộ': 'Hà Nội',
    'Nghệ A': 'Nghệ An',
    'Hải Dươn': 'Hải Dương',
    'TP Huế': 'Thừa Thiên Huế'
}

# Hàm chuẩn hóa văn bản (chuyển thành chữ thường và loại bỏ dấu)
def normalize_text(text):
    return unidecode(text.lower()).strip()

# Hàm để trích xuất tỉnh từ địa chỉ và thay thế sai sót chính tả trực tiếp trong địa chỉ
def extract_province_from_address(address, province_list):
    # Thay thế các từ sai chính tả trong địa chỉ (nếu có)
    for wrong, correct in replacement_dict.items():
        address = address.replace(wrong, correct)

    # Chuẩn hóa địa chỉ sau khi thay thế sai chính tả
    address_clean = normalize_text(address)

    best_match = None

    # Duyệt qua tất cả các tỉnh và tìm kiếm tỉnh trong địa chỉ
    for _, row in province_list.iterrows():
        province_name = normalize_text(row['province_name'])  # Chuẩn hóa tên tỉnh
        if province_name in address_clean:
            best_match = row['province_name']
            break  # Dừng khi tìm thấy tỉnh đầu tiên

    return best_match

# Áp dụng hàm trích xuất tỉnh từ địa chỉ cho từng khách hàng
customers_df['province_name_extract'] = customers_df['address'].apply(lambda x: extract_province_from_address(x, provinces_df))

# Gán province_id dựa trên province_name_extract
customers_df = customers_df.merge(provinces_df, left_on='province_name_extract', right_on='province_name', how='left')

# Tạo file CSV cho những khách hàng đã thành công trong việc ánh xạ tỉnh
success_df = customers_df[customers_df['province_id'].notnull()]
success_df.to_csv('data/processed/success_mapping_customer_province.csv', index=False)

# Tạo file CSV cho những khách hàng không ánh xạ được tỉnh
failed_df = customers_df[customers_df['province_id'].isnull()]
failed_df.to_csv('data/processed/failed_mapping_customer_province.csv', index=False)

# Đóng kết nối
cur.close()
conn.close()
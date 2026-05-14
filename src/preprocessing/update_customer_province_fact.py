import psycopg2
import pandas as pd
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

# Đọc dữ liệu từ CSV chứa province_id và province_name đã ánh xạ thành công
success_df = pd.read_csv('data/processed/cleaned/success_mapping_customer_province.csv')

# Cập nhật bảng customer với province_id từ CSV
for index, row in success_df.iterrows():
    province_name = row['province_name_extract']
    province_id = row['province_id']
    
    # Cập nhật bảng customer để gán province_id
    cur.execute("""
        UPDATE customer 
        SET province_id = %s 
        WHERE customer_code = %s;
    """, (province_id, row['customer_code']))

# Cập nhật bảng fact_sales với province_id từ CSV
for index, row in success_df.iterrows():
    province_name = row['province_name_extract']
    province_id = row['province_id']
    
    # Cập nhật bảng fact_sales để gán province_id
    cur.execute("""
        UPDATE fact_sales fs
        SET province_id = %s, province_name = %s
        FROM customer c
        WHERE fs.customer_code = c.customer_code
        AND c.province_id = %s;
    """, (province_id, province_name, province_id))

# Cam kết thay đổi
conn.commit()

# Đóng kết nối
cur.close()
conn.close()
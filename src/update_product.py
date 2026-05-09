import psycopg2
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'tnbike_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, database=DB_NAME,
    user=DB_USER, password=DB_PASSWORD,
    client_encoding='UTF8'
)
cur = conn.cursor()
cur.execute("SET search_path TO tnbike, public;")

query = "SELECT product_code, product_name, color FROM product"
df = pd.read_sql(query, conn)

def clean_color(color):
    if color and any(c.isalpha() for c in color):
        return color.title()
    return None

df['color'] = df['color'].apply(clean_color)

df.to_csv('data/processed/product_cleaned.csv', index=False, encoding='utf-8-sig')

cur.close()
conn.close()
print("Data cleaning completed and saved to data/processed/product_cleaned.csv")
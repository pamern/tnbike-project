import logging
import psycopg2
import pandas as pd
from dotenv import load_dotenv
import os

# Set up logging
logging.basicConfig(filename='update_log.log', level=logging.DEBUG)

# Load environment variables from .env file
load_dotenv()

# Get database credentials from environment
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'tnbike_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

conn = None
cur = None

try:
    # Establish database connection
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )
    cur = conn.cursor()

    # Set the search path to the tnbike schema
    cur.execute("SET search_path TO tnbike, public;")

    # Load the cleaned product data from the CSV file
    df_cleaned = pd.read_csv(r'data/processed/cleaned/product_cleaned.csv', dtype={'product_code': str})  # Ensure product_code is read as string
    logging.info(f"Loaded {len(df_cleaned)} rows from the CSV.")

    # Clean the product_code column (ensure it's treated as a string and remove leading/trailing spaces)
    df_cleaned['product_code'] = df_cleaned['product_code'].str.strip()

    # Prepare the data for batch update
    update_data = []
    for index, row in df_cleaned.iterrows():
        product_code = row['product_code']
        color_new = row['color_new']
        update_data.append((color_new, product_code))

    # Update the color column in the product table using the color_new column
    update_query = """
        UPDATE product
        SET color = %s
        WHERE product_code = %s;
    """
    cur.executemany(update_query, update_data)

    # Update the color in fact_sales if the product is used in an order
    update_fact_sales_query = """
        UPDATE fact_sales
        SET color = %s
        WHERE product_code = %s;
    """
    cur.executemany(update_fact_sales_query, update_data)

    # Commit the transaction to save the changes
    conn.commit()
    logging.info(f"Changes committed to the database.")

except Exception as e:
    if conn:
        conn.rollback()  # Rollback if error occurs
    logging.error(f"Error: {str(e)}")

finally:
    # Ensure that connection and cursor are closed only if they were initialized
    if cur:
        cur.close()
    if conn:
        conn.close()
    logging.info("Database connection closed.")
    print("Color update completed successfully.")
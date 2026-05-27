# ============================================================
# CUSTOMER FORECAST (BG/NBD)
# Script hóa từ DE_HM_C_fix_93.ipynb
# ============================================================

import logging
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from lifetimes import BetaGeoFitter
from lifetimes.utils import calibration_and_holdout_data, summary_data_from_transaction_data

from src.config.settings import PROCESSED_DIR, ensure_dir, get_database_settings


# ============================================================
# CẤU HÌNH LOGGING VÀ THAM SỐ
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Ngày kết thúc dataset và ngày kết thúc calibration cho model
OBSERVATION_END = pd.Timestamp("2026-03-31")
CALIBRATION_END = pd.Timestamp("2026-02-28")

OUTPUT_DIR = ensure_dir(PROCESSED_DIR / "forecast/results/customer_forecast")
OUTPUT_CSV_PATH = OUTPUT_DIR / "customer_forecast.csv"


# ============================================================
# HÀM XỬ LÝ DỮ LIỆU
# ============================================================

def load_fact_sales_from_postgres() -> pd.DataFrame:
    """
    Đọc dữ liệu `fact_sales` từ PostgreSQL (DB chạy trong Docker qua `docker-compose`).

    Lấy cấu hình từ `.env` ở root project:
    `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, (optional) `DB_SCHEMA`.
    """
    try:
        import psycopg2
    except ImportError as exc:
        raise ImportError(
            "Thiếu thư viện để kết nối PostgreSQL. Cài thêm: pip install psycopg2-binary"
        ) from exc

    db = get_database_settings()

    conn_kwargs = {
        "host": db.host,
        "port": int(db.port),
        "dbname": db.database,
        "user": db.user,
        "password": db.password,
    }

    query = f"""
        SELECT
            customer_code,
            customer_name,
            province_name,
            region,
            so_number,
            order_date,
            line_total
        FROM {db.schema}.fact_sales
        WHERE order_date IS NOT NULL
    """

    logger.info(
        "Đang đọc fact_sales từ PostgreSQL (%s:%s/%s, schema=%s)...",
        db.host,
        db.port,
        db.database,
        db.schema,
    )
    with psycopg2.connect(**conn_kwargs) as conn:
        return pd.read_sql_query(query, conn)


def preprocess_orders(df_fact: pd.DataFrame) -> pd.DataFrame:
    """Khử trùng lặp về cấp độ đơn hàng từ fact_sales."""
    logger.info("Chuyển đổi dữ liệu về cấp độ đơn hàng...")
    df_orders = df_fact.groupby(["customer_code", "so_number"]).agg(
        order_date=("order_date", "first"),
        customer_name=("customer_name", "first"),
        province_name=("province_name", "first"),
        region=("region", "first"),
        order_revenue=("line_total", "sum"),
    ).reset_index()

    df_orders["order_date"] = pd.to_datetime(df_orders["order_date"])
    return df_orders


def create_summary_table(df_orders: pd.DataFrame) -> pd.DataFrame:
    """Tạo bảng Summary chuẩn BG/NBD gồm Frequency, Recency, T."""
    logger.info("Tạo summary table (frequency, recency, T)...")
    df_summary = summary_data_from_transaction_data(
        df_orders,
        customer_id_col="customer_code",
        datetime_col="order_date",
        observation_period_end=OBSERVATION_END,
        freq="W",
    )

    # Gắn thêm thông tin đại lý
    customer_info = (
        df_orders.groupby("customer_code")
        .agg(
            customer_name=("customer_name", "first"),
            region=("region", "first"),
            province_name=("province_name", "first"),
            total_revenue=("order_revenue", "sum"),
        )
        .reset_index()
    )

    df_summary = df_summary.reset_index().merge(customer_info, on="customer_code", how="left")
    return df_summary


def fit_predict_bgnbd(df_summary: pd.DataFrame, df_orders: pd.DataFrame) -> pd.DataFrame:
    """Train BG/NBD và dự báo Probability Alive + số đơn mua trong 30 ngày tới."""
    logger.info("Huấn luyện BG/NBD (Beta-Geometric/Negative Binomial Distribution)...")

    bgf_final = BetaGeoFitter(penalizer_coef=0.01)
    bgf_final.fit(
        df_summary["frequency"],
        df_summary["recency"],
        df_summary["T"],
        verbose=False,
    )

    forecast_days = 30
    forecast_weeks = forecast_days / 7

    logger.info("Dự báo Probability Alive và Predicted Orders (30 days)...")
    df_summary["prob_alive"] = bgf_final.conditional_probability_alive(
        df_summary["frequency"],
        df_summary["recency"],
        df_summary["T"],
    )
    df_summary["predicted_orders_30d"] = (
        bgf_final.conditional_expected_number_of_purchases_up_to_time(
            forecast_weeks,
            df_summary["frequency"],
            df_summary["recency"],
            df_summary["T"],
        )
    )

    # Lọc nhóm New Dealer: chỉ xuất hiện sau CALIBRATION_END
    logger.info("Xác định nhóm New Dealer...")
    summary_cal_holdout = calibration_and_holdout_data(
        df_orders,
        customer_id_col="customer_code",
        datetime_col="order_date",
        calibration_period_end=CALIBRATION_END,
        observation_period_end=OBSERVATION_END,
        freq="W",
    )

    all_customers = set(df_orders["customer_code"].unique())
    cal_customers = set(summary_cal_holdout.index)
    new_dealers = all_customers - cal_customers

    df_summary["is_new_dealer"] = df_summary["customer_code"].isin(new_dealers)

    # Xử lý NaN (T=0, chưa có lịch sử recency)
    df_summary["predicted_orders_30d"] = df_summary["predicted_orders_30d"].fillna(0)
    df_summary["prob_alive"] = df_summary["prob_alive"].fillna(0)

    return df_summary


def classify_dealer(row: pd.Series) -> str:
    """Phân nhóm đại lý dựa vào logic hiện tại."""
    if row["is_new_dealer"]:
        return "New Dealer"
    if row["prob_alive"] >= 0.7 and row["predicted_orders_30d"] >= 1.0:
        return "Likely to Buy"
    elif row["prob_alive"] >= 0.5 and row["predicted_orders_30d"] >= 0.5:
        return "Monitor High"
    elif row["prob_alive"] >= 0.5:
        return "Monitor Low"
    elif row["prob_alive"] >= 0.3:
        return "At Risk"
    else:
        return "Likely Churned"


def add_priorities_and_segment(df_summary: pd.DataFrame) -> pd.DataFrame:
    """Phân lớp đại lý thành các nhóm hành động tương ứng."""
    logger.info("Phân loại Segment và tính Priority Score...")
    df_summary["segment"] = df_summary.apply(classify_dealer, axis=1)

    # Priority = xác suất sống sót x dự báo số đơn mua
    df_summary["priority_score"] = (df_summary["prob_alive"] * df_summary["predicted_orders_30d"]).round(4)

    # Sắp xếp đại lý tiềm năng cao nhất lên trên
    df_summary = df_summary.sort_values("priority_score", ascending=False)
    return df_summary


# ============================================================
# HÀM CHÍNH
# ============================================================

def run_forecast_pipeline() -> None:
    logger.info("BẮT ĐẦU CHẠY PIPELINE FORECAST KHÁCH HÀNG")
    try:
        # Bước 1: Đọc fact_sales từ DB (Docker/PostgreSQL)
        df_fact = load_fact_sales_from_postgres()

        # Bước 2 & 3: Xử lý cấp đơn hàng và tạo summary
        df_orders = preprocess_orders(df_fact)
        df_summary = create_summary_table(df_orders)

        # Bước 4: Fit model và dự báo
        df_summary = fit_predict_bgnbd(df_summary, df_orders)

        # Bước 5: Phân cấp & tính điểm
        df_summary = add_priorities_and_segment(df_summary)

        # Bước 6: Lưu kết quả
        logger.info("Lưu kết quả dự báo ra: %s", OUTPUT_CSV_PATH)
        df_summary.to_csv(OUTPUT_CSV_PATH, index=False)

        logger.info("HOÀN TẤT QUY TRÌNH!")
    except Exception as e:
        logger.error("Lỗi trong quá trình chạy pipeline forecast: %s", e, exc_info=True)


if __name__ == "__main__":
    run_forecast_pipeline()


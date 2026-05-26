# ============================================================
# CUSTOMER RFM SEGMENTATION
# Source : tnbike.v_customer_activity
# Output : tnbike.customer_rfm_segment
# Run    : python -m src.analytics.rfm_segmentation
# ============================================================

from __future__ import annotations

import os
import logging
from datetime import date
from typing import Optional, Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

import psycopg2
from psycopg2.extras import execute_values


# ============================================================
# 1. LOGGING
# ============================================================

try:
    from src.config.logging_config import setup_logging

    setup_logging()
    logger = logging.getLogger(__name__)

except Exception:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger(__name__)


# ============================================================
# 2. DATABASE CONNECTION
# ============================================================

def get_db_connection():
    """
    Ưu tiên dùng connection helper có sẵn trong project.
    Nếu project chưa có hoặc tên hàm khác, fallback về .env.
    """

    try:
        from src.utils.db_connection import get_connection # type: ignore

        logger.info("Using src.utils.db_connection.get_connection()")
        return get_connection()

    except Exception:
        logger.info("Fallback to psycopg2 connection from .env")

    load_dotenv()

    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=os.getenv("PGPORT", "5432"),
        database=os.getenv("PGDATABASE", "tnbike_db"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )


# ============================================================
# 3. CONFIG
# ============================================================

SCHEMA_NAME = "tnbike"
SOURCE_VIEW = "v_customer_activity"
TARGET_TABLE = "customer_rfm_segment"

N_CLUSTERS = 4
RANDOM_STATE = 42
NO_ORDER_RECENCY_DAYS = 9999


# ============================================================
# 4. SQL
# ============================================================

CREATE_TARGET_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.{TARGET_TABLE} (
    customer_code           VARCHAR(20) NOT NULL,
    snapshot_date           DATE NOT NULL,

    customer_name           VARCHAR(200),
    province_id             INTEGER,
    province_name           VARCHAR(100),
    region                  VARCHAR(50),

    total_orders            INTEGER,
    total_revenue           NUMERIC(15,2),
    first_order_date        DATE,
    last_order_date         DATE,
    days_since_last_order   INTEGER,

    recency                 NUMERIC(15,4),
    frequency               NUMERIC(15,4),
    monetary                NUMERIC(15,4),

    cluster_id              INTEGER,
    segment_name            VARCHAR(80),
    segment_rank            INTEGER,

    silhouette_score        NUMERIC(10,6),

    created_at              TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (customer_code, snapshot_date)
);
"""

DELETE_CURRENT_SNAPSHOT_SQL = f"""
DELETE FROM {SCHEMA_NAME}.{TARGET_TABLE}
WHERE snapshot_date = %s;
"""

INSERT_TARGET_SQL = f"""
INSERT INTO {SCHEMA_NAME}.{TARGET_TABLE} (
    customer_code,
    snapshot_date,

    customer_name,
    province_id,
    province_name,
    region,

    total_orders,
    total_revenue,
    first_order_date,
    last_order_date,
    days_since_last_order,

    recency,
    frequency,
    monetary,

    cluster_id,
    segment_name,
    segment_rank,

    silhouette_score
)
VALUES %s;
"""


# ============================================================
# 5. EXTRACT
# ============================================================

def load_customer_activity(conn) -> pd.DataFrame:
    """
    Đọc dữ liệu từ view v_customer_activity.

    Không dùng days_since_last_order từ view để tính RFM,
    vì view thường tính theo CURRENT_DATE nên kết quả sẽ tự trôi theo ngày.
    Recency sẽ được tính lại theo snapshot_date = MAX(last_order_date).
    """

    query = f"""
        SELECT
            customer_code,
            customer_name,
            province_id,
            province_name,
            region,
            COALESCE(total_orders, 0) AS total_orders,
            COALESCE(total_revenue, 0) AS total_revenue,
            first_order_date,
            last_order_date
        FROM {SCHEMA_NAME}.{SOURCE_VIEW};
    """

    df = pd.read_sql(query, conn)

    logger.info("Loaded customer activity: %s rows", len(df))

    return df


# ============================================================
# 6. FEATURE ENGINEERING
# ============================================================

def get_snapshot_date(df: pd.DataFrame) -> date:
    """
    Ngày chốt RFM = ngày mua gần nhất trong dữ liệu.
    Cách này giúp RFM ổn định, không bị thay đổi theo ngày chạy script.
    """

    if "last_order_date" not in df.columns:
        raise ValueError("Missing required column: last_order_date")

    last_order_dates = pd.to_datetime(df["last_order_date"], errors="coerce")

    if last_order_dates.dropna().empty:
        raise ValueError(
            "Cannot determine snapshot_date because all last_order_date values are null"
        )

    snapshot_date = last_order_dates.max().date()

    logger.info("RFM snapshot_date = %s", snapshot_date)

    return snapshot_date


def prepare_rfm_features(
    df: pd.DataFrame,
    snapshot_date: date,
) -> tuple[pd.DataFrame, np.ndarray]:
    required_cols = [
        "customer_code",
        "last_order_date",
        "total_orders",
        "total_revenue",
    ]

    missing_cols = [col for col in required_cols if col not in df.columns]

    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")

    rfm_df = df.copy()

    rfm_df["first_order_date"] = pd.to_datetime(
        rfm_df["first_order_date"],
        errors="coerce",
    )

    rfm_df["last_order_date"] = pd.to_datetime(
        rfm_df["last_order_date"],
        errors="coerce",
    )

    snapshot_ts = pd.Timestamp(snapshot_date)

    rfm_df["recency"] = (
        snapshot_ts - rfm_df["last_order_date"]
    ).dt.days

    rfm_df["recency"] = (
        rfm_df["recency"]
        .fillna(NO_ORDER_RECENCY_DAYS)
        .astype(float)
    )

    # Cột business-friendly cho Power BI
    rfm_df["days_since_last_order"] = rfm_df["recency"].astype(int)

    rfm_df["frequency"] = rfm_df["total_orders"].fillna(0).astype(float)
    rfm_df["monetary"] = rfm_df["total_revenue"].fillna(0).astype(float)

    # Chỉ dùng cho KMeans, không lưu vào DB
    model_features = pd.DataFrame(
        {
            "recency": rfm_df["recency"],
            "frequency": np.log1p(rfm_df["frequency"]),
            "monetary": np.log1p(rfm_df["monetary"]),
        }
    )

    scaler = StandardScaler()
    scaled_features = scaler.fit_transform(model_features)

    logger.info(
        "RFM feature summary:\n%s",
        rfm_df[["recency", "frequency", "monetary"]]
        .describe()
        .round(2)
        .to_string(),
    )

    return rfm_df, scaled_features


# ============================================================
# 7. CLUSTERING
# ============================================================

def train_kmeans(
    scaled_features: np.ndarray,
    n_clusters: int = N_CLUSTERS,
) -> tuple[np.ndarray, Optional[float]]:
    if len(scaled_features) < n_clusters:
        raise ValueError(
            f"Not enough customers for clustering. "
            f"Rows={len(scaled_features)}, n_clusters={n_clusters}"
        )

    model = KMeans(
        n_clusters=n_clusters,
        random_state=RANDOM_STATE,
        n_init=10,
    )

    clusters = model.fit_predict(scaled_features)

    score = None

    if len(set(clusters)) > 1:
        score = float(silhouette_score(scaled_features, clusters))

    logger.info("KMeans completed with %s clusters", n_clusters)

    if score is not None:
        logger.info("Silhouette score: %.4f", score)

    return clusters, score


# ============================================================
# 8. SEGMENT NAMING
# ============================================================

def assign_segment_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Đặt tên cụm dựa trên profile trung bình, không phụ thuộc cứng vào cluster_id.

    Logic:
    - VIP Customers: frequency + monetary cao, recency thấp
    - Active Customers: còn mua gần đây, giá trị khá
    - At Risk Customers: lâu chưa mua
    - Low Value Customers: giá trị và tần suất thấp
    """

    result = df.copy()

    summary = (
        result.groupby("cluster_id")
        .agg(
            avg_recency=("recency", "mean"),
            avg_frequency=("frequency", "mean"),
            avg_monetary=("monetary", "mean"),
            customer_count=("customer_code", "count"),
        )
        .reset_index()
    )

    # Điểm tốt: Monetary cao, Frequency cao, Recency thấp
    summary["value_score"] = (
        summary["avg_monetary"].rank(method="dense", ascending=True)
        + summary["avg_frequency"].rank(method="dense", ascending=True)
        + summary["avg_recency"].rank(method="dense", ascending=False)
    )

    summary = summary.sort_values(
        "value_score",
        ascending=False,
    ).reset_index(drop=True)

    segment_labels = [
        ("VIP Customers", 1),
        ("Active Customers", 2),
        ("At Risk Customers", 3),
        ("Low Value Customers", 4),
    ]

    cluster_mapping = {}

    for idx, row in summary.iterrows():
        label, rank = segment_labels[idx]
        cluster_mapping[int(row["cluster_id"])] = {
            "segment_name": label,
            "segment_rank": rank,
        }

    result["segment_name"] = result["cluster_id"].map(
        lambda x: cluster_mapping[int(x)]["segment_name"]
    )

    result["segment_rank"] = result["cluster_id"].map(
        lambda x: cluster_mapping[int(x)]["segment_rank"]
    )

    logger.info("Cluster profile:")
    logger.info("\n%s", summary.to_string(index=False))

    segment_summary = (
        result.groupby(["cluster_id", "segment_name"])
        .agg(
            days_since_last_order=("days_since_last_order", "mean"),
            total_orders=("total_orders", "mean"),
            total_revenue=("total_revenue", "mean"),
            customer_count=("customer_code", "count"),
        )
        .reset_index()
    )

    logger.info("Segment summary:")
    logger.info("\n%s", segment_summary.round(2).to_string(index=False))

    return result


# ============================================================
# 9. LOAD
# ============================================================

def create_target_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TARGET_TABLE_SQL)

    conn.commit()

    logger.info("Ensured target table exists: %s.%s", SCHEMA_NAME, TARGET_TABLE)


def to_python_value(value: Any) -> Any:
    """
    Convert numpy / pandas scalar sang Python native để psycopg2 insert ổn định.
    """

    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.date()

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        return float(value)

    if isinstance(value, np.bool_):
        return bool(value)

    return value


def save_rfm_segments(
    conn,
    df: pd.DataFrame,
    snapshot_date: date,
    model_score: Optional[float],
) -> None:
    output_cols = [
        "customer_code",
        "snapshot_date",

        "customer_name",
        "province_id",
        "province_name",
        "region",

        "total_orders",
        "total_revenue",
        "first_order_date",
        "last_order_date",
        "days_since_last_order",

        "recency",
        "frequency",
        "monetary",

        "cluster_id",
        "segment_name",
        "segment_rank",

        "silhouette_score",
    ]

    output_df = df.copy()
    output_df["snapshot_date"] = snapshot_date
    output_df["silhouette_score"] = model_score

    output_df = output_df[output_cols]

    rows = [
        tuple(to_python_value(value) for value in row)
        for row in output_df.to_numpy()
    ]

    with conn.cursor() as cur:
        cur.execute(DELETE_CURRENT_SNAPSHOT_SQL, (snapshot_date,))
        execute_values(cur, INSERT_TARGET_SQL, rows)

    conn.commit()

    logger.info(
        "Saved %s RFM segment rows to %s.%s with snapshot_date=%s",
        len(rows),
        SCHEMA_NAME,
        TARGET_TABLE,
        snapshot_date,
    )


# ============================================================
# 10. MAIN PIPELINE
# ============================================================

def run_rfm_segmentation() -> None:
    conn = None

    try:
        logger.info("Starting RFM segmentation pipeline")

        conn = get_db_connection()

        create_target_table(conn)

        df = load_customer_activity(conn)

        if df.empty:
            logger.warning("No customer activity data found. Stop pipeline.")
            return

        snapshot_date = get_snapshot_date(df)

        rfm_df, scaled_features = prepare_rfm_features(
            df=df,
            snapshot_date=snapshot_date,
        )

        clusters, score = train_kmeans(
            scaled_features=scaled_features,
            n_clusters=N_CLUSTERS,
        )

        rfm_df["cluster_id"] = clusters

        rfm_df = assign_segment_names(rfm_df)

        save_rfm_segments(
            conn=conn,
            df=rfm_df,
            snapshot_date=snapshot_date,
            model_score=score,
        )

        logger.info("RFM segmentation pipeline completed successfully")

    except Exception as exc:
        logger.exception("RFM segmentation pipeline failed: %s", exc)
        raise

    finally:
        if conn is not None:
            conn.close()
            logger.info("Database connection closed")


if __name__ == "__main__":
    run_rfm_segmentation()
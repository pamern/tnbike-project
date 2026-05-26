import os
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

# ============================================================
# 0. Path cấu hình
# ============================================================
BASE_DIR = Path(__file__).resolve().parents[1]  # tnbike-project
FORECAST_DIR = BASE_DIR / "data" / "processed" / "forecasting_adjusted"
OUTPUT_DIR = FORECAST_DIR / "charts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# ============================================================
# 1. Load forecast Q2
# ============================================================
monthly = pd.read_csv(FORECAST_DIR / "forecast_monthly_q2_adjusted.csv")

# ============================================================
# 2. Load lịch sử T1, T2, T3/2026 từ PostgreSQL
# ============================================================
conn = psycopg2.connect(
    host=os.getenv("PGHOST", "localhost"),
    port=os.getenv("PGPORT", "5432"),
    database=os.getenv("PGDATABASE", "tnbike_db"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", "postgres")
)

sql = """
SELECT
    fiscal_month,
    SUM(quantity) AS actual_qty,
    SUM(line_total) AS actual_revenue
FROM tnbike.fact_sales
WHERE fiscal_year = 2026
  AND fiscal_month IN (1, 2, 3)
GROUP BY fiscal_month
ORDER BY fiscal_month;
"""

history = pd.read_sql(sql, conn)
conn.close()

# ============================================================
# 3. Chuẩn hóa dữ liệu forecast
# ============================================================
monthly_q2 = monthly.copy()

monthly_q2["forecast_revenue_base_bil"] = monthly_q2["forecast_revenue_base"] / 1e9

# ============================================================
# 4. Tạo dữ liệu vẽ chart T1-T6
# ============================================================
history_plot = history.rename(columns={
    "actual_qty": "qty",
    "actual_revenue": "revenue"
})

history_plot["type"] = "Thực tế"
history_plot["month_label"] = history_plot["fiscal_month"].apply(lambda x: f"T{int(x)}")

forecast_plot = monthly_q2.rename(columns={
    "forecast_qty_base": "qty",
    "forecast_revenue_base": "revenue"
})

forecast_plot["type"] = "Dự báo"
forecast_plot["month_label"] = forecast_plot["fiscal_month"].apply(lambda x: f"T{int(x)}")

plot_df = pd.concat(
    [
        history_plot[["fiscal_month", "month_label", "qty", "revenue", "type"]],
        forecast_plot[["fiscal_month", "month_label", "qty", "revenue", "type"]]
    ],
    ignore_index=True
).sort_values("fiscal_month")

plot_df["x"] = range(len(plot_df))

# ============================================================
# 5. Vẽ chart: Thực tế T1-T3 + Dự báo T4-T6
# ============================================================
plt.figure(figsize=(10, 5.5))

# Dữ liệu thực tế
actual_df = plot_df[plot_df["type"] == "Thực tế"]

plt.plot(
    actual_df["x"],
    actual_df["qty"],
    marker="o",
    linewidth=2.5,
    label="Sản lượng thực tế"
)

# Dữ liệu dự báo
forecast_df = plot_df[plot_df["type"] == "Dự báo"]

plt.plot(
    forecast_df["x"],
    forecast_df["qty"],
    marker="o",
    linewidth=2.5,
    linestyle="--",
    label="Sản lượng dự báo"
)

# Nối điểm T3 thực tế với T4 dự báo để nhìn liền mạch
bridge_df = plot_df[plot_df["fiscal_month"].isin([3, 4])]

plt.plot(
    bridge_df["x"],
    bridge_df["qty"],
    linewidth=1.5,
    linestyle=":",
    label="Chuyển tiếp thực tế → dự báo"
)

# Vùng kịch bản dự báo T4-T6
forecast_x = forecast_df["x"].values

plt.fill_between(
    forecast_x,
    monthly_q2["forecast_qty_conservative"].values,
    monthly_q2["forecast_qty_optimistic"].values,
    alpha=0.18,
    label="Khoảng Conservative - Optimistic"
)

# Label số lượng
for _, row in plot_df.iterrows():
    plt.text(
        row["x"],
        row["qty"],
        f'{row["qty"]:,.0f} xe',
        ha="center",
        va="bottom",
        fontsize=9
    )

# Highlight tháng 5
may_row = plot_df[plot_df["fiscal_month"] == 5].iloc[0]

plt.scatter(
    may_row["x"],
    may_row["qty"],
    s=120,
    zorder=5
)

plt.text(
    may_row["x"],
    may_row["qty"] * 1.06,
    "Cao điểm dự báo",
    ha="center",
    fontsize=10,
    fontweight="bold"
)

plt.xticks(plot_df["x"], plot_df["month_label"])
plt.title("Sản lượng thực tế T1-T3 và dự báo Q2/2026")
plt.xlabel("Tháng năm 2026")
plt.ylabel("Sản lượng, xe")
plt.legend()
plt.tight_layout()

output_path = OUTPUT_DIR / "01_forecast_qty_by_month.png"
plt.savefig(output_path, dpi=300)
plt.show()

print(f"Đã lưu chart tại: {output_path}")
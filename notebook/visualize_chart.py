import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# ============================================================
# 0. Path cấu hình
# ============================================================
BASE_DIR = Path(__file__).resolve().parents[1]  # tnbike-project
FORECAST_DIR = BASE_DIR / "data" / "processed" / "forecasting_adjusted"
OUTPUT_DIR = FORECAST_DIR / "charts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 1. Load dữ liệu
# ============================================================
monthly = pd.read_csv(FORECAST_DIR / "forecast_monthly_q2_adjusted.csv")
group = pd.read_csv(FORECAST_DIR / "forecast_group_q2_adjusted.csv")
top20 = pd.read_csv(FORECAST_DIR / "top20_sku_q2_adjusted.csv")

# ============================================================
# 2. Chuẩn hóa đơn vị
# ============================================================
monthly["revenue_base_bil"] = monthly["forecast_revenue_base"] / 1e9
monthly["revenue_conservative_bil"] = monthly["forecast_revenue_conservative"] / 1e9
monthly["revenue_optimistic_bil"] = monthly["forecast_revenue_optimistic"] / 1e9

group["revenue_bil"] = group["forecast_revenue_q2"] / 1e9
group["revenue_share_pct"] = group["revenue_share_q2"] * 100

top20["revenue_bil"] = top20["forecast_revenue_q2"] / 1e9

# ============================================================
# 3. Biểu đồ 1: Dự báo sản lượng theo tháng
# Insight: Tháng 5 là cao điểm
# ============================================================
plt.figure(figsize=(9, 5))

month_labels = monthly["fiscal_month"].apply(lambda x: f"Tháng {int(x)}/2026")

plt.plot(
    month_labels,
    monthly["forecast_qty_base"],
    marker="o",
    linewidth=2,
    label="Sản lượng dự báo"
)

plt.fill_between(
    month_labels,
    monthly["forecast_qty_conservative"],
    monthly["forecast_qty_optimistic"],
    alpha=0.2,
    label="Khoảng Conservative - Optimistic"
)

for i, row in monthly.reset_index(drop=True).iterrows():
    plt.text(
        i,
        row["forecast_qty_base"],
        f'{row["forecast_qty_base"]:,.0f} xe',
        ha="center",
        va="bottom",
        fontsize=9
    )

plt.title("Dự báo sản lượng Q2/2026: Tháng 5 là cao điểm")
plt.xlabel("Tháng")
plt.ylabel("Sản lượng dự báo")
plt.legend()
plt.tight_layout()

plt.savefig(OUTPUT_DIR / "01_forecast_qty_by_month.png", dpi=300)
plt.show()

# ============================================================
# 4. Biểu đồ 2: Cơ cấu doanh thu theo nhóm sản phẩm
# Insight: Xe phổ thông chủ lực, Unknown group cần xử lý
# ============================================================
group_plot = group.sort_values("forecast_revenue_q2", ascending=True)

plt.figure(figsize=(9, 5))

plt.barh(group_plot["group_name"], group_plot["revenue_bil"])

for i, row in group_plot.reset_index(drop=True).iterrows():
    plt.text(
        row["revenue_bil"],
        i,
        f'{row["revenue_bil"]:.1f} tỷ | {row["revenue_share_pct"]:.1f}%',
        va="center",
        fontsize=9
    )

plt.title("Cơ cấu doanh thu dự báo Q2/2026 theo nhóm sản phẩm")
plt.xlabel("Doanh thu dự báo, tỷ đồng")
plt.ylabel("Nhóm sản phẩm")
plt.tight_layout()

plt.savefig(OUTPUT_DIR / "02_forecast_revenue_by_group.png", dpi=300)
plt.show()

# ============================================================
# 5. Biểu đồ 3: Top 20 SKU theo doanh thu
# Insight: Doanh số tập trung vào SKU trọng điểm
# ============================================================
top_sku_plot = top20.sort_values("forecast_revenue_q2", ascending=True).copy()

top_sku_plot["sku_label"] = (
    top_sku_plot["product_name"]
    .str.replace("Xe đạp Thống Nhất ", "", regex=False)
    .str.replace("Xe đạp thống nhất ", "", regex=False)
)

plt.figure(figsize=(10, 8))

plt.barh(top_sku_plot["sku_label"], top_sku_plot["revenue_bil"])

for i, row in top_sku_plot.reset_index(drop=True).iterrows():
    plt.text(
        row["revenue_bil"],
        i,
        f'{row["revenue_bil"]:.1f}',
        va="center",
        fontsize=8
    )

plt.title("Top 20 SKU đóng góp doanh thu dự báo Q2/2026")
plt.xlabel("Doanh thu dự báo, tỷ đồng")
plt.ylabel("SKU")
plt.tight_layout()

plt.savefig(OUTPUT_DIR / "03_top20_sku_forecast_revenue.png", dpi=300)
plt.show()

print(f"Đã lưu biểu đồ tại: {OUTPUT_DIR}")
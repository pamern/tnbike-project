# TNBIKE Intelligence — Tổng hợp tài liệu dự án

## 1. Giới thiệu dự án

TNBIKE Intelligence là hệ thống ETL/ELT tích hợp thêm tầng phân tích AI, mô hình dự báo, báo cáo chiến lược và dashboard web cục bộ.

Mục tiêu của hệ thống là tự động hóa quy trình:
1. Trích xuất đơn hàng từ email và PDF
2. Chuẩn hóa dữ liệu khách hàng, màu sắc, tỉnh/thành
3. Nạp dữ liệu vào PostgreSQL schema `tnbike`
4. Tạo báo cáo AI và dự báo kinh doanh phục vụ quyết định

---

## 2. Cấu hình và cài đặt thư viện

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-14+-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://www.docker.com/)

### Cài đặt nhanh

```powershell
# 1. Clone repository
git clone https://github.com/pamern/tnbike-project
cd tnbike-project

# 2. Tạo môi trường ảo
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Cài dependencies chính và AI/UI
python -m pip install -r requirements.txt -r ai\requirements.txt -r ui\requirements.txt

# 4. Khởi động database
docker compose up -d

# 5. Sao chép file môi trường AI (nếu cần)
copy ai\.env.example ai\.env

# 6. Chạy dry-run AI pipeline
.\.venv\Scripts\python -m ai.run_ai_pipeline --dry-run

# 7. Khởi động dashboard
.\.venv\Scripts\python -m uvicorn ui.app:app --host 127.0.0.1 --port 8501

# 8. Mở giao diện trên trình duyệt
Start-Process "http://127.0.0.1:8501"
```

### Biến môi trường quan trọng

```text
PGHOST=localhost
PGPORT=5432
PGDATABASE=tnbike_db
PGUSER=postgres
PGPASSWORD=postgres
DB_SCHEMA=tnbike

GROQ_API_KEYS=
GROQ_MODEL=llama-3.3-70b-versatile
UI_PORT=8501
UI_SESSION_SECRET=change-me-random-32-chars
```

> Lưu ý: các key API của AI có thể được nhập trực tiếp trên giao diện UI, chỉ tồn tại trong session server, không ghi xuống file `.env`.

---

## 3. Cấu trúc thư mục

```text
tnbike-project/
├── ai/                    # Tầng AI: BI, forecasting, report, LLM
├── ui/                    # FastAPI dashboard + WebSocket + templates
├── src/                   # ETL/ELT gốc (không sửa trực tiếp)
├── sql/                   # Schema PostgreSQL
├── schedules/             # Scheduler pipeline
├── data/                  # Input / staging / backup
├── logs/                  # Log hệ thống
├── reports/               # Hình ảnh và báo cáo
├── dashboard/             # File Power BI
└── README.md              # Tài liệu chính
```

---

## 4. Kiến trúc tổng thể

![Pipeline overview](reports/img/pipeline.png)

### 4.1. Xử lý đơn hàng tự động

Hệ thống tự động đọc file `.eml` và PDF đính kèm, trích xuất dữ liệu đơn hàng, customer, sản phẩm và ghi vào staging tables.

Quy trình chính gồm:
- Trích xuất email và attachment
- Chuẩn hóa dữ liệu khách hàng, màu sắc, tỉnh/thành
- Nạp dữ liệu vào PostgreSQL
- Cập nhật `fact_sales` phục vụ phân tích và dashboard

### 4.2. Mô hình dự đoán

Tầng forecasting sử dụng dữ liệu từ `fact_sales` và `sales_order` để tạo:
- Dự báo doanh số theo nhóm sản phẩm và màu sắc
- Chấm điểm rủi ro / churn của đại lý
- Báo cáo chiến lược với mức độ tin cậy và khuyến nghị hành động

### 4.3. Hệ thống tích hợp AI

AI layer bao gồm:
- BI interpreter: chuyển dữ liệu DB thành ngữ cảnh cho mô hình LLM
- Forecasting reasoner: diễn giải kết quả mô hình và đề xuất chiến lược
- Report engine: render HTML/Markdown báo cáo tổng hợp
- FastAPI UI: theo dõi realtime pipeline qua WebSocket và chạy AI jobs

---

## 5. CLI Usages

### Chạy pipeline ETL gốc

```powershell
# Chạy pipeline ETL chính
python -m src.pipeline.run_pipeline

# Chạy preprocessing
python -m src.preprocessing.run_preprocessing

# Dry-run preprocessing
python -m src.preprocessing.run_preprocessing --dry-run

# Bỏ qua bước chuẩn hóa tỉnh / màu
python -m src.preprocessing.run_preprocessing --skip-province --skip-color

# Chỉ map customer thiếu province
python -m src.preprocessing.run_preprocessing --only-missing-customer-province

# Không reset province trước khi map
python -m src.preprocessing.run_preprocessing --no-reset-customer-province

# Không refresh toàn bộ fact_sales
python -m src.preprocessing.run_preprocessing --no-refresh-fact-all

# Chạy pipeline với giới hạn 5 email
python -m src.pipeline.run_pipeline --limit 5

# Dry-run pipeline (không thay đổi DB)
python -m src.pipeline.run_pipeline --dry-run

# Tạo restore point timestamped
python -m src.pipeline.run_pipeline --timestamp-restore-point

# Di chuyển file ngay cả khi dùng --limit
python -m src.pipeline.run_pipeline --limit 5 --move-on-limit

# Rollback khi lỗi xảy ra
python -m src.pipeline.run_pipeline --rollback-on-fail

# Bỏ qua cập nhật fact_sales
python -m src.pipeline.run_pipeline --skip-update-fact
```

### Chạy AI pipeline

```powershell
.\.venv\Scripts\python -m ai.run_ai_pipeline
.\.venv\Scripts\python -m ai.run_ai_pipeline --dry-run
.\.venv\Scripts\python -m ai.run_ai_pipeline --branch bi
.\.venv\Scripts\python -m ai.run_ai_pipeline --branch predict
```

### Chạy report engine

```powershell
.\.venv\Scripts\python -m ai.report.report_runner --dry-run
```

### Chạy scheduler

```powershell
# Chạy scheduler theo cấu hình mặc định
python schedules/run_pipeline_scheduler.py

# Chạy scheduler với file config cụ thể
python schedules/run_pipeline_scheduler.py --config schedules/pipeline_schedule.yaml

# Ghi đè chế độ chạy
python schedules/run_pipeline_scheduler.py --mode watch

# Chạy AI scheduler độc lập (hook sau ETL)
.\.venv\Scripts\python -m ai.scheduler --run-once
.\.venv\Scripts\python -m ai.scheduler --run-once --dry-run
```

### Chạy dashboard UI

```powershell
.\.venv\Scripts\python -m uvicorn ui.app:app --host 127.0.0.1 --port 8501
```

### Chạy test

```powershell
.\.venv\Scripts\python -m pytest ai\tests\ -q
```

---

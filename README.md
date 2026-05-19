# TNBIKE Project

> **ETL/ELT Pipeline cho TNBIKE**  
> Tự động xử lý email → PostgreSQL DWH → Power BI Dashboard

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-14+-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://www.docker.com/)

---
## 🎯 Tổng quan

Hệ thống tự động thực hiện các tác vụ:

1. **Trích xuất** sales orders từ tệp đính kèm của email (`.eml` + PDF)
2. **Chuẩn hoá** dữ liệu (khách hàng, tỉnh/thành, màu sản phẩm)
3. **Nạp** dữ liệu giao dịch vào PostgreSQL (schema: `tnbike`)
4. **Cập nhật** `fact_sales`, phục vụ phân tích kinh doanh

---
## ⚡ Thao tác nhanh

### Yêu cầu tối thiểu

- Python 3.10+
- Docker & Docker Compose
- Git
- ~2GB dung lượng trống

### 5 bước chính

Chuẩn bị dữ liệu cho Power BI:
```powershell
# 1. Clone và vào thư mục project
git clone <repo-url>
cd tnbike-project

# 2. Tạo virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Cài dependencies
pip install -r requirements.txt

# 4. Khởi động database (PostgreSQL + Adminer)
docker compose up -d

# 5. (Tuỳ chọn) Restore DB từ backup
py -m src.database.backup restore --input data/backup/restore_db/<ten_file_backup.dump>

```

---
## ⚙️ Cấu hình

### Các biến môi trường

| Biến          | Mặc định    | Mô tả               |
| ------------- | ----------- | ------------------- |
| `PGHOST`      | `localhost` | PostgreSQL host     |
| `PGPORT`      | `5432`      | PostgreSQL port     |
| `PGDATABASE`  | `tnbike_db` | Database name       |
| `PGUSER`      | `postgres`  | DB username         |
| `PGPASSWORD`  | `postgres`  | DB password         |
| `DB_SCHEMA`   | `tnbike`    | Schema mặc định     |
| `DB_MIN_CONN` | `1`         | Số kết nối tối thiểu |
| `DB_MAX_CONN` | `5`         | Số kết nối tối đa    |

## 🚀 Quy trình xử lý hoàn chỉnh

```powershell
# 1. Clone và vào thư mục project
git clone <repo-url>
cd tnbike-project

# 2. Tạo virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Cài dependencies
pip install -r requirements.txt

# 4. Init database
.\scripts\database.ps1 -Action init

# 5. Preprocessing (all steps)
python -m src.preprocessing.run_preprocessing

# 6. Run pipeline (all steps)
python -m src.pipeline.run_pipeline --rollback-on-fail
```
**Preprocessing (tuỳ chọn)**
```powershell
# Dry-run (chỉ in lệnh, không thực thi)
python -m src.preprocessing.run_preprocessing --dry-run

# Skip specific steps
python -m src.preprocessing.run_preprocessing --skip-province --skip-color

# Only map customers without province
python -m src.preprocessing.run_preprocessing --only-missing-customer-province

# Không reset customer.province_id trước khi map
python -m src.preprocessing.run_preprocessing --no-reset-customer-province

# Không rebuild fact_sales toàn bộ (không truyền --all cho update_fact_sales)
python -m src.preprocessing.run_preprocessing --no-refresh-fact-all
```

**Chạy pipeline (tuỳ chọn)**
```powershell
# Test with 5 emails only
python -m src.pipeline.run_pipeline --limit 5

# Dry-run (no DB changes)
python -m src.pipeline.run_pipeline --dry-run

# Create timestamped restore point
python -m src.pipeline.run_pipeline --timestamp-restore-point

# Move files even with --limit
python -m src.pipeline.run_pipeline --limit 5 --move-on-limit

# Auto-rollback on failure
python -m src.pipeline.run_pipeline --rollback-on-fail

# Skip updating fact_sales
python -m src.pipeline.run_pipeline --skip-update-fact
```

### 3. Vận hành cơ sở dữ liệu 

#### Backup dữ liệu

```powershell
py -m src.database.backup backup --format dump --timestamp
```
#### Restore dữ liệu

```powershell
# Backup dạng dump
py -m src.database.backup backup --format dump --timestamp
# Hoặc dạng sql
py -m src.database.backup backup --format sql --timestamp
```

#### Giao diện cơ sở dữ liệu

Đường dẫn http://localhost:8080 và thực hiện đăng nhập theo cấu hình ở file .env

#### Reset cơ sở dữ liệu (CẨN THẬN)

```powershell
# Remove all data and volumes
docker compose down -v

# Restart fresh
docker compose up -d
```

### 4. Debugging 

```powershell
# Xem log chính
Get-Content logs/run_pipeline.log -Tail 50

# Xem error log
Get-Content logs/error.log

# Xem lỗi extract
Get-Content data/processed/quality_check/extract_fail.csv

# Xem lỗi load (legacy nếu có)
Get-Content data/processed/staging/staging_fail.csv

# Rollback thủ công
python -m src.pipeline.fallback restore-db
```

### 5. Power BI 

**File:** `dashboard/tnbike_dashboard.pbix`

**Steps:**

1. Open Power BI Desktop
2. Click "Get Data" → PostgreSQL
3. Connection settings:
   - Server: `localhost:5432`
   - Database: `tnbike_db`
4. Load tables: `fact_sales`, `sales_order`, `order_line`
5. Click "Refresh" to update data

---

## ⏱️ Scheduling

### Tổng quan

Scheduler cho phép tự động chạy pipeline theo nhiều chế độ:

- **Manual**: chạy 1 lần theo yêu cầu
- **Watch**: tự chạy khi có file `.eml` mới trong thư mục incoming
- **Interval**: chạy mỗi N phút
- **Fixed Time**: chạy theo giờ cố định trong tuần

### File cấu hình

**Vị trí**: `schedules/pipeline_schedule.yaml`

#### Mode 1: Manual (chạy 1 lần)

```powershell
python schedules/run_pipeline_scheduler.py
# or
python -m schedules.run_pipeline_scheduler
```

**Phù hợp**: test nhanh, chạy thủ công

#### Mode 2: Watch (tự chạy khi có file mới)

```yaml
mode: watch
watch:
  poll_seconds: 10
  stable_seconds: 5
```

```powershell
python schedules/run_pipeline_scheduler.py
```

**Cách hoạt động:**
1. Poll thư mục `data/incoming/eml/` mỗi 10 giây
2. Khi phát hiện `.eml` mới, đợi 5 giây để file copy xong
3. Tự động chạy pipeline
4. Di chuyển `.eml` đã xử lý sang success/failed
5. Tiếp tục canh thư mục

**Phù hợp**: vận hành liên tục trong giờ làm việc

#### Mode 3: Interval (mỗi N phút)

```yaml
mode: interval
interval:
  every_minutes: 60
```

```powershell
python schedules/run_pipeline_scheduler.py
```

**Phù hợp**: chạy theo batch định kỳ (ví dụ mỗi giờ)

#### Mode 4: Fixed Time (chạy theo giờ cố định)

```yaml
mode: fixed_time
fixed_time:
  times:
    - "08:00"
    - "12:00"
    - "17:30"
  days:
    - mon
    - tue
    - wed
    - thu
    - fri
```

### Scheduler Options

```powershell
# Dùng file config cụ thể
python schedules/run_pipeline_scheduler.py --config schedules/pipeline_schedule.yaml

# Ghi đè mode
python schedules/run_pipeline_scheduler.py --mode watch
```
---
## 📁 Cấu trúc thư mục

```
tnbike-project/
│
├── README.md                      # Tài liệu hướng dẫn
├── requirements.txt               # Thư viện Python
├── docker-compose.yml             # PostgreSQL + Adminer
└── .env                           # Biến môi trường
│
├── src/                           # Mã nguồn
│   ├── __init__.py
│   ├── constants.py              # Hằng số dùng chung
│   ├── types.py                  # Định nghĩa kiểu dữ liệu
│   │
│   ├── config/                   # Cấu hình
│   │   ├── __init__.py
│   │   ├── settings.py           # Thiết lập & đường dẫn
│   │   └── logging_config.py     # Cấu hình ghi log
│   │
│   ├── utils/                    # Tiện ích
│   │   ├── __init__.py
│   │   ├── time_utils.py         # Tiện ích thời gian
│   │   ├── file_utils.py         # Tiện ích xử lý file
│   │   └── executor.py           # Chạy từng bước (bước)
│   │
│   ├── database/                 # Tầng database
│   │   ├── connection.py         # Kết nối DB / pool
│   │   └── backup.py             # Backup/restore DB
│   │
│   ├── pipeline/                 # Pipeline ETL chính
│   │   ├── __init__.py
│   │   ├── run_pipeline.py       # Điều phối pipeline ⭐
│   │   ├── extract_to_staging.py # Trích xuất email
│   │   ├── load_staging_to_db.py # Load dữ liệu vào DB
│   │   ├── update_fact_sales.py  # Cập nhật fact_sales
│   │   ├── move_processed_file.py # Di chuyển file đã xử lý
│   │   ├── fallback.py           # Fallback/rollback DB
│   │   └── email_extractor.py    # Phân tích PDF/đơn hàng
│   │
│   ├── preprocessing/            # Chuẩn hoá dữ liệu master
│   │   ├── __init__.py
│   │   ├── run_preprocessing.py  # Điều phối preprocessing
│   │   ├── standardize_province.py     # Chuẩn hoá tỉnh/thành
│   │   ├── map_customer_province.py    # Map tỉnh/thành cho customer
│   │   └── standardize_color.py        # Chuẩn hoá màu
│   │
│   └── analystics/               # Phân tích
│       └── overview.ipynb        # Notebook phân tích
│
├── sql/                          # Schema DB / script SQL
│   ├── 01_create_tables.sql      # Tạo bảng
│   ├── 02_import_data.sql        # Import dữ liệu ban đầu
│   ├── 03_create_email_log.sql   # Tạo bảng email_log
│   └── 04_standardize_province.sql # Chuẩn hoá tỉnh/thành
│
├── schedules/                    # Lịch chạy pipeline
│   ├── run_pipeline_scheduler.py # Chương trình scheduler ⚙️
│   └── pipeline_schedule.yaml    # Cấu hình lịch
│
├── data/                         # Dữ liệu (thường bỏ qua bởi git)
│   ├── incoming/eml/             # Email đầu vào (thư mục watch)
│   ├── processed/
│   │   ├── staging/              # CSV staging
│   │   ├── quality_check/        # Lỗi trích xuất
│   │   ├── cleaned/              # Dữ liệu đã làm sạch (nếu có)
│   │   ├── success_eml/eml/      # Email xử lý OK
│   │   └── failed_eml/eml/       # Email lỗi
│   └── backup/                   # Backup/restore point của DB
│
├── logs/                         # Log chạy (thường bỏ qua bởi git)
│   ├── run_pipeline.log
│   ├── error.log
│   └── run_preprocessing.log
│
├── dashboard/
│   └── tnbike_dashboard.pbix     # Dashboard Power BI
│
└── reports/                      # Báo cáo xuất ra
    └── img/pipeline.png          # Sơ đồ pipeline
```

---

## 🔄 Pipeline Flow

```
Input: *.eml files in data/incoming/eml/
                    ↓
┌─────────────────────────────────────────┐
│ STEP 1: EXTRACT                         │
│ - Read .eml files                       │
│ - Parse PDF attachments                 │
│ - Extract order/customer/product info   │
│ - Generate quality check results        │
└─────────────────────────────────────────┘
                    ↓
        Output: staging CSVs
        ├─ staging_sales_order.csv
        ├─ staging_order_line.csv
        ├─ staging_customer.csv
        ├─ staging_email_log.csv
        ├─ staging_fail.csv (errors)
        └─ extract_fail.csv (quality issues)
                    ↓
┌─────────────────────────────────────────┐
│ STEP 2: LOAD                            │
│ - Validate data quality                 │
│ - Upsert to PostgreSQL                  │
│ - Log processing results                │
└─────────────────────────────────────────┘
                    ↓
        Output: Updated database tables
        ├─ tnbike.sales_order (upserted)
        ├─ tnbike.order_line (upserted)
        ├─ tnbike.customer (upserted)
        └─ tnbike.email_log (inserted)
                    ↓
┌─────────────────────────────────────────┐
│ STEP 3: REFRESH FACT                    │
│ - Delete old fact_sales rows            │
│ - Recalculate from base tables          │
│ - Update fact_sales                     │
└─────────────────────────────────────────┘
                    ↓
        Output: Refreshed fact table
        └─ tnbike.fact_sales
                    ↓
┌─────────────────────────────────────────┐
│ STEP 4: ORGANIZE FILES                  │
│ - Successful emails → success folder    │
│ - Failed emails → failed folder         │
└─────────────────────────────────────────┘
                    ↓
Output: Organized .eml files
├─ data/processed/success_eml/eml/ ✓
└─ data/processed/failed_eml/eml/ ✗
```

### Processing Status Values

| Status         | Ý nghĩa              | Nơi ghi nhận                | Hành động              |
| -------------- | -------------------- | --------------------------- | ---------------------- |
| `PROCESSING`   | Đang xử lý           | `email_log`                 | Pipeline đang chạy     |
| `SUCCESS`      | Xử lý thành công     | `email_log`                 | Move sang success      |
| `NEEDS_REVIEW` | Cần kiểm tra thủ công | `email_log`                 | Move sang success      |
| `FAILED`       | Lỗi xử lý            | `email_log`                 | Move sang failed       |

---

## 📄 License

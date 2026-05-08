# tnbike-project

## 1. Khởi tạo env

### 1.1. Tạo venv

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 1.2. Cài thư viện

```powershell
python -m pip install -U pip
pip install -r requirements.txt
```

### 1.3. Cấu hình `.env`

Tạo file `.env` ở root project (hoặc sửa file `.env` hiện có) với các biến tối thiểu:

- `PGHOST` (ví dụ: `localhost`)
- `PGPORT` (mặc định: `5432`)
- `PGDATABASE` (mặc định: `tnbike_db`)
- `PGUSER` (mặc định: `postgres`)
- `PGPASSWORD` (mặc định: `postgres`)

Python scripts trong `src/` sẽ đọc các biến này bằng `python-dotenv`.

## 2. Khởi tạo docker

Tại thư mục root (có `docker-compose.yml`):

```powershell
docker compose up -d
docker ps
```

Postgres chạy ở `localhost:5432` (mặc định: user `postgres`, pass `postgres`, db `tnbike_db`).

Adminer chạy ở `http://localhost:8080` để quản trị DB trên local.

Thông tin login Adminer (PostgreSQL):
- System: `PostgreSQL`
- Server: `postgres` (nếu Adminer chạy trong Docker Compose) hoặc `localhost` (nếu bạn chạy Adminer ngoài Docker)
- Username: `postgres`
- Password: `postgres`
- Database: `tnbike_db`

## 3. Import dữ liệu

### 3.1. Tạo bảng

```powershell
docker cp sql/01_create_tables.sql tnbike_postgres:/01_create_tables.sql
docker exec -it tnbike_postgres psql -U postgres -d tnbike_db -f /01_create_tables.sql
```

### 3.2. Import dữ liệu ban đầu

```powershell
docker cp sql/02_import_data.sql tnbike_postgres:/02_import_data.sql
docker exec -it tnbike_postgres psql -U postgres -d tnbike_db -f /02_import_data.sql
```

## 4. Xử lý dữ liệu

Chạy extract để đọc email/PDF tháng 3/2026 và xuất ra các file staging CSV trong `data/staging/`.

Input mặc định:
- `data/raw/tnbike_emails_mar2026/`

Output:
- `data/staging/staging_email_log.csv`
- `data/staging/staging_sales_order.csv`
- `data/staging/staging_order_line.csv`
- (tùy trường hợp) `data/staging/staging_customer.csv`, `data/staging/staging_customer_log.csv`
- file lỗi: `data/staging/staging_fail.csv`, `data/staging/staging_fail_summary.csv`

```powershell
python src/extract_data.py
```

## 5. Import log, ghi dữ liệu 3/2026

### 5.1. Tạo bảng email log

```powershell
docker cp sql/03_create_email_log.sql tnbike_postgres:/03_create_email_log.sql
docker exec -it tnbike_postgres psql -U postgres -d tnbike_db -f /03_create_email_log.sql
```

### 5.1a. Chú thích `email_log.processing_status`

File `data/staging/staging_email_log.csv` có cột `processing_status` (được ghi bởi `python src/extract_data.py`). Ý nghĩa:

- `STARTED`: bắt đầu xử lý email (đã parse header/body, chưa kết luận).
- `SUCCESS`: xử lý thành công, có ghi `sales_order` + `order_line`, không có lỗi kèm theo.
- `PARTIAL_SUCCESS`: xử lý được nhưng có phát sinh lỗi dòng (`staging_fail.csv`) hoặc phát sinh customer mới cần staging.
- `FAILED_NO_ATTACHMENT`: không tìm thấy file PDF đính kèm.
- `FAILED_VALIDATION`: header đơn hàng thiếu trường bắt buộc nên không ghi `sales_order/order_line`.
- `FAILED_NO_LINES`: không trích xuất được dòng hàng nào từ PDF nên không ghi `sales_order/order_line`.
- `FAILED_NO_VALID_LINES`: có parse line nhưng không còn line hợp lệ (ví dụ product_code không có trong master) nên không ghi `sales_order`.
- `FAILED_CUSTOMER`: không tạo/lấy được `customer_code` nên không ghi `sales_order/order_line`.

Ghi chú: `NEW_CUSTOMER` là status dùng cho `staging_customer_log.csv` (không phải `email_log.processing_status`).

### 5.2. Import staging CSV vào DB (email_log / sales_order / order_line)

```powershell
python src/import_staging_to_db.py
```

## 6. Đồng bộ fact_sales

Chạy refresh `fact_sales` cho tháng 03/2026 (xóa/insert lại để tránh duplicate):

```powershell
docker cp sql/04_refresh_fact_sales_03_2026.sql tnbike_postgres:/04_refresh_fact_sales_03_2026.sql
docker exec -it tnbike_postgres psql -U postgres -d tnbike_db -f /04_refresh_fact_sales_03_2026.sql
```

## 7. Connect DB với Power BI

### 7.1. Thông tin kết nối (mặc định)

- Server: `localhost`
- Port: `5432`
- Database: `tnbike_db`
- Username: `postgres`
- Password: `postgres`

### 7.2. Kết nối trong Power BI Desktop

1. `Home` → `Get data` → `PostgreSQL database`.
2. Nhập:
   - `Server`: `localhost:5432`
   - `Database`: `tnbike_db`
3. Chọn mode: `Import` (khuyến nghị).
4. Khi được hỏi credentials, chọn `Database` và nhập user/password ở trên.
5. Trong Navigator, chọn schema `tnbike` và các bảng cần dùng (thường: `fact_sales`, `sales_order`, `order_line`, `customer`, `product`...).

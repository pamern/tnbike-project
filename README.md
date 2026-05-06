# tnbike-project

README ngắn gọn để: khởi tạo PostgreSQL bằng Docker, tạo bảng, import dữ liệu ban đầu, cấu hình `venv`, và cài thư viện Python.

## 1. Khởi tạo Docker (PostgreSQL)

Tại thư mục root (có `docker-compose.yml`):

```powershell
docker compose up -d
docker ps
```

Postgres chạy ở `localhost:5432` (mặc định: user `postgres`, pass `postgres`, db `tnbike_db`).

## 2. Tạo bảng

```powershell
docker cp sql/01_create_tables.sql tnbike_postgres:/01_create_tables.sql
docker exec -it tnbike_postgres psql -U postgres -d tnbike_db -f /01_create_tables.sql
```

## 3. Import dữ liệu ban đầu

```powershell
docker cp sql/02_import_data.sql tnbike_postgres:/02_import_data.sql
docker exec -it tnbike_postgres psql -U postgres -d tnbike_db -f /02_import_data.sql
```

(Tuỳ chọn) tạo bảng log email:

```powershell
docker cp sql/03_create_email_log.sql tnbike_postgres:/03_create_email_log.sql
docker exec -it tnbike_postgres psql -U postgres -d tnbike_db -f /03_create_email_log.sql
```

## 4. Cấu hình venv

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

## 5. Cài thư viện

```powershell
python -m pip install -U pip
pip install -r requirements.txt
```


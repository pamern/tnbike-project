# TNBIKE Intelligence

TNBIKE Intelligence adds an AI analysis, forecasting, strategic reporting, and local web dashboard layer around the existing TNBIKE ETL project.

The original ETL code remains in `src/`, `sql/`, `schedules/`, and `dashboard/`. New AI code lives in `ai/`, and the local control UI lives in `ui/`.

## Architecture

```text
Incoming EML/PDF
      |
      v
ETL + preprocessing
      |
      v
PostgreSQL schema: tnbike
      |
      +--> ai/interpreters    -> BI context + Groq interpretation
      |
      +--> ai/forecasting     -> demand forecast + churn scoring
      |
      +--> ai/report          -> strategic HTML/Markdown report
      |
      v
ui FastAPI dashboard + WebSocket stepper
```

## Quick Start

Run these commands from the repository root.

```powershell
docker compose up -d
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r ai\requirements.txt -r ui\requirements.txt
copy ai\.env.example ai\.env
.\.venv\Scripts\python -m ai.run_ai_pipeline --dry-run
.\.venv\Scripts\python -m uvicorn ui.app:app --host 127.0.0.1 --port 8501
```

Open `http://127.0.0.1:8501`.

## UI Usage

Use `Settings` to enter Groq API keys and optional DB connection values. Keys are stored only in the server-side session and are not written to `.env`.

Use `Control Center` to run:

```text
All
BI only
Predict only
Report only
Dry-run
```

The WebSocket stepper shows:

```text
1. Kết nối DB
2. Trích xuất dữ liệu
3. Phân tích BI
4. Dự báo ML
5. Lập luận AI
6. Xuất báo cáo
```

Use `Reports` to view and download generated HTML/Markdown reports from `ai/report/output/`.

## CLI Usage

```powershell
ai\.venv\Scripts\python -m ai.run_ai_pipeline
ai\.venv\Scripts\python -m ai.run_ai_pipeline --dry-run
ai\.venv\Scripts\python -m ai.run_ai_pipeline --branch bi
ai\.venv\Scripts\python -m ai.run_ai_pipeline --branch predict
ai\.venv\Scripts\python -m ai.report.report_runner --dry-run
```

## Configuration

AI defaults are in `ai/.env.example`.

UI-only defaults are in `ui/.env.example`.

Important variables:

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

## Existing ETL Pipeline

The existing ETL project owns source extraction, preprocessing, database schema, and Docker infrastructure. The AI layer imports or queries existing outputs and does not modify `src/`.

Useful existing commands:

```powershell
docker compose up -d
python -m src.pipeline.run_pipeline
python -m src.preprocessing.run_preprocessing
```

## Tests

```powershell
.\.venv\Scripts\python -m pytest ai\tests\ -q
```

For UI smoke tests:

```powershell
.\.venv\Scripts\python -m uvicorn ui.app:app --host 127.0.0.1 --port 8501
```

Then open `http://127.0.0.1:8501`, run a dry-run, and open the report viewer.

## Notes

Do not commit real `.env` files, `keys.md`, generated reports, logs, database backups, or incoming customer data.

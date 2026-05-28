# TNBIKE Codebase Map

Generated: 2026-05-28

## Scope Read

Read in the required order where present:

1. `tnbike-project/sql/01_create_tables.sql`
2. `tnbike-project/src/constants.py`
3. `tnbike-project/src/types.py`
4. `tnbike-project/src/config/settings.py`
5. `tnbike-project/src/database/connection.py`
6. `tnbike-project/src/pipeline/email_extractor.py`
7. `tnbike-project/src/pipeline/run_pipeline.py`
8. `tnbike-project/src/preprocessing/run_preprocessing.py`
9. `tnbike-project/schedules/pipeline_schedule.yaml`
10. `tnbike-project/src/analystics/overview.ipynb`

Note: `src/analystics/overview.ipynb` does not exist in the current checkout. The repo contains `src/analytics/` and `notebook/rfm_clustered.ipynb` instead. This setup keeps source files unchanged and records the mismatch here.

## Database Schema

Schema: `tnbike`

Core tables and views:

- `product_group(group_code PK, group_name, description, created_at)`
- `product_line(line_id PK, line_name, group_code FK, created_at)`
- `product(product_code PK, product_name, line_id FK, color, unit, is_active, created_at)`
- `product_price(price_id PK, product_code FK, unit_price, effective_from, effective_to, created_at)`
- `province(province_id PK, province_name UNIQUE, region, created_at)`
- `customer(customer_code PK, customer_name, tax_code, address, province_id FK, customer_tier, is_active, created_at, updated_at)`
- `sales_order(order_id PK, so_number UNIQUE, invoice_symbol, invoice_number, order_date, customer_code FK, total_amount, total_quantity, line_count, fiscal_year, fiscal_month, fiscal_quarter, created_at)`
- `order_line(line_id PK, order_id FK, so_number, product_code FK, quantity, unit_price, line_total, created_at)`
- `fact_sales(fact_id PK, order_date, fiscal_year, fiscal_quarter, fiscal_month, week_of_year, so_number, order_id, line_id, customer fields, product fields, quantity, unit_price, line_total)`
- `v_monthly_by_group`: monthly revenue, quantity, order count by product group.
- `v_customer_period`: quarterly customer aggregates for RFM and churn analysis.
- `v_sku_monthly`: SKU/color monthly aggregates for variant trend analysis.
- `v_customer_activity`: customer activity summary with days since last order.

Important trigger:

- `fn_update_order_totals()` updates `sales_order.total_amount`, `total_quantity`, and `line_count` when `order_line` changes.

## Constants

From `src/constants.py`:

- CSV names: `STAGING_SALES_ORDER_CSV`, `STAGING_ORDER_LINE_CSV`, `STAGING_CUSTOMER_CSV`, `STAGING_EMAIL_LOG_CSV`, `STAGING_FAIL_CSV`, `STAGING_FAIL_SUMMARY_CSV`, `STANDARDIZED_COLOR_CSV`, mapping CSV names, `EXTRACT_FAIL_CSV`.
- SQL names: `SQL_CREATE_TABLES`, `SQL_IMPORT_DATA`, `SQL_CREATE_EMAIL_LOG`, `SQL_STANDARDIZE_PROVINCE`, `SQL_CLEAN_PROVINCE`.
- Status values: `STATUS_PROCESSING`, `STATUS_SUCCESS`, `STATUS_NEEDS_REVIEW`, `STATUS_FAILED`.
- Mapping and quality statuses: `MAPPING_AUTO_MAPPED`, `MAPPING_MANUAL_MAPPED`, `MAPPING_FAILED`, `MAPPING_PENDING`, `QUALITY_CHECK_SUCCESS`, `QUALITY_CHECK_WARNING`, `QUALITY_CHECK_FAILED`.
- Default schema: `DEFAULT_SCHEMA = "tnbike"`.
- Log files: `LOG_PIPELINE`, `LOG_PREPROCESSING`, `LOG_ERROR`.

## Types

From `src/types.py`:

- `StepResult(TypedDict)`: generic step status with `step`, `status`, `elapsed`, `returncode`, `error`, `result`, `reason`.
- `ExtractResult(TypedDict)`: extraction counts and output directories.
- `LoadDBResult(TypedDict)`: DB load counts and duration.
- `UpdateFactSalesResult(TypedDict)`: fact rebuild counts.
- `MoveFilesResult(TypedDict)`: processed file move summary.
- `PipelineSummary(TypedDict)`: end-to-end ETL summary.
- `PreprocessingStepResult(TypedDict)` and `PreprocessingSummary(TypedDict)`.

## Settings And Paths

From `src/config/settings.py`:

- `PROJECT_ROOT = Path(__file__).resolve().parents[2]`, so the root is `tnbike-project/`.
- `.env` is loaded from `tnbike-project/.env`.
- Helpers: `get_env(name, default=None)`, `get_required_env(name)`, `get_env_bool(name, default=False)`, `get_env_int(name, default)`, `resolve_project_path(path)`, `ensure_dir(path)`.
- Data paths: `DATA_DIR`, `BACKUP_DIR`, `INCOMING_EML_DIR`, `STAGING_DIR`, `QUALITY_CHECK_DIR`, `CLEANED_DIR`, `MAPPING_DIR`, success/failed EML dirs, `LOG_DIR`, `SQL_DIR`, `SCHEDULES_DIR`, `PIPELINE_SCHEDULE_CONFIG`.
- DB config is exposed by `get_database_settings() -> DatabaseSettings(host, port, database, user, password, schema)`.
- Pipeline paths are exposed by `get_pipeline_path_settings() -> PipelinePathSettings`.
- `ensure_project_dirs()` creates all project runtime directories.

## Database Connection

From `src/database/connection.py`:

- `get_db_config() -> dict`
- `init_connection_pool() -> None`
- `close_connection_pool() -> None`
- `get_connection()` context manager: initializes pool if needed, sets `search_path` to `DB_SCHEMA, public`, commits on success and rolls back on exception.
- `get_cursor(dict_cursor: bool = False)` context manager: uses `RealDictCursor` when requested.
- `test_connection() -> bool`
- `fetch_one(query: str, params: Optional[tuple] = None) -> Optional[Any]`
- `fetch_all(query: str, params: Optional[tuple] = None) -> list`
- `execute_query(query: str, params: Optional[tuple] = None) -> int`
- `execute_many(query: str, params_list: list[tuple]) -> int`
- `table_exists(table_name: str, schema: str = DB_SCHEMA) -> bool`
- `get_table_row_count(table_name: str) -> int`

AI modules should import these helpers by adding `tnbike-project/` to `sys.path` at runtime, then importing `src.database.connection`. They should not reimplement the pool.

## Email Extraction

From `src/pipeline/email_extractor.py`:

- `clean_text(value: str | None) -> str`
- `clean_multiline_text(value: str | None) -> str`
- `sanitize_filename(filename: str) -> str`
- `parse_email_file(path: str | Path)`
- `parse_email_datetime(raw_date: str | None) -> str`
- `parse_email_date(raw_date: str | None) -> str`
- `strip_html(html: str) -> str`
- `get_email_body(msg) -> str`
- `extract_pdf_text_from_bytes(pdf_bytes: bytes) -> tuple[str, str]`
- `extract_pdf_attachments(msg, extract_text: bool = True) -> list[dict]`
- `extract_email(eml_path: str | Path, extract_pdf_text: bool = True) -> dict`

This module is low-level extraction only: it reads one `.eml`, extracts email metadata/body/PDF attachment text, and returns a dict. It does not validate orders, look up DB records, or write files.

## Main Pipeline

From `src/pipeline/run_pipeline.py`:

- `run_step(step_name: str, func, *args, **kwargs)`
- `print_step_summary(title: str, summary: dict | None) -> None`
- `run_pipeline(...) -> dict`
- `parse_args() -> argparse.Namespace`
- `main() -> None`

Pipeline flow:

1. Optional restore point.
2. `extract_emails_to_staging(...)`.
3. `load_staging_to_db(...)`.
4. Skip remaining DB/file mutations if `dry_run=True`.
5. `update_fact_sales(...)`.
6. `move_processed_files(...)`.

Important operational flags: `limit`, `skip_restore_point`, `timestamp_restore_point`, `dry_run`, `skip_update_fact`, `refresh_fact_all`, `skip_move_files`, `move_on_limit`, `rollback_on_fail`.

## Preprocessing

From `src/preprocessing/run_preprocessing.py`:

- `module_exists(module_path: str) -> bool`
- `run_command(step_name: str, command: list[str], dry_run: bool = False) -> dict`
- `build_module_command(module_name: str, args: list[str] | None = None) -> list[str]`
- `run_preprocessing(...) -> dict`
- `parse_args() -> argparse.Namespace`
- `main() -> None`

Default preprocessing flow:

1. Optional restore point.
2. `src.preprocessing.standardize_province`
3. `src.preprocessing.map_customer_province --update-db --reset-before-update`
4. `src.preprocessing.standardize_color --update-db`
5. `src.pipeline.update_fact_sales --all`

## Schedule

From `schedules/pipeline_schedule.yaml`:

- Scheduler enabled.
- Current mode: `fixed_time`.
- Fixed times: `08:00`, `17:10`.
- Active days: Monday through Saturday.
- Pipeline paths point to `data/incoming/eml`, `data/processed/staging`, `data/processed/quality_check`, success and failed EML directories.
- `rollback_on_fail: true`.

Integration option for later phases: either extend this YAML with an AI trigger after ETL or keep `ai/scheduler.py` independent to avoid modifying original project config.

## Existing Analytics References

The repo includes `src/analytics/`:

- `rfm_segmentation.py`: reads `v_customer_activity`, trains KMeans-based RFM clusters, creates/saves `tnbike.customer_rfm_segment`.
- `customer_forecast.py`: reads `fact_sales`, prepares order-level data, uses BG/NBD from `lifetimes`, and writes `data/processed/forecast/results/customer_forecast/customer_forecast.csv`.
- `forecasting_revenue.py`: prepares SKU-month panel from `fact_sales`, builds lag/rolling/YoY features, backtests tree models and benchmarks, forecasts Q2/2026, calibrates by product group, and writes forecast CSVs.

These are useful references for AI forecasting modules, but AI implementation should live under `ai/`.

## AI Integration Hook Points

- DB reads: use `src.database.connection.fetch_all()` or `get_cursor(dict_cursor=True)` for BI extractors.
- BI query sources: `fact_sales`, `v_monthly_by_group`, `v_customer_period`, `v_sku_monthly`, `v_customer_activity`.
- Forecast feature sources: `fact_sales`, `sales_order`, `order_line`, `customer`, `product`, and existing views.
- ETL completion hook: run `python -m ai.run_ai_pipeline` after `src.pipeline.run_pipeline` completes successfully.
- Report output: keep generated HTML/Markdown under `ai/report/output/`.

## Setup Status

- `tnbike-project/` already exists, so clone was skipped.
- AI files are created under `ai/` only.
- DB and Docker verification still depend on local Docker/PostgreSQL availability.


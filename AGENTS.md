# AGENTS.md — TNBIKE AI Integration System

> **Dành cho:** Coding Agent (Codex/ Copilot / Cursor / Windsurf / Aider / …)  
> **Mục đích:** Hướng dẫn tự hành để xây dựng tầng AI trên codebase TNBIKE hiện có  
> **Cập nhật cuối:** tự động sau mỗi checkpoint hoàn thành

---

## 0. Đọc trước khi làm bất cứ điều gì

```
WORKSPACE LAYOUT (FINAL)
tnbike-project/          ← git repo https://github.com/pamern/tnbike-project.git
    src/
        pipeline/        ← ETL đã hoàn chỉnh (đồng đội viết)
        preprocessing/   ← chuẩn hoá dữ liệu (đồng đội viết)
        database/        ← connection pool + backup (đồng đội viết)
        analytics/       ← notebook phân tích (đồng đội viết) [NOTE: không phải "analystics"]
        config/
        utils/
    sql/                 ← schema PostgreSQL
    schedules/           ← scheduler YAML
    dashboard/           ← tnbike_dashboard.pbix (Power BI)
    data/                ← incoming EML, staging CSV, backup
    logs/
    ai/                  ← tầng AI — nằm trong repo để push cùng source
    interpreters/        ← LLM interpretation modules
    forecasting/         ← predictive models
    report/              ← automated report generation
    tests/
    llm_client.py        ← Groq multi-key rotation
    scheduler.py         ← standalone trigger sau ETL
    ui/                  ← FastAPI localhost dashboard
    app.py               ← FastAPI backend
    static/              ← CSS, JS thuần
    templates/           ← Jinja2 HTML pages
    ws/                  ← WebSocket event emitter
```

**Quy tắc bất biến:**
1. **Không sửa code trong `tnbike-project/src/`** — chỉ import, không override.
2. Mọi file AI đặt trong `ai/`. Mọi file UI đặt trong `ui/`.
3. Mỗi lần sửa lỗi: sửa tối thiểu, ghi log, tiếp tục — không refactor lan rộng.
4. Khi gặp lỗi không tự giải được sau 2 lần thử: ghi vào `ai/logs/pending_issues.log` rồi dừng bước đó, chuyển sang task tiếp theo.
5. Sau mỗi checkpoint: tự cập nhật mục **Status** trong file này.
6. **Không push lên `main` hoặc `master`** — mọi thay đổi chỉ đẩy lên nhánh `nhat`.

---

## 1. Đọc hiểu codebase (Bước khởi đầu bắt buộc)

Trước khi viết bất kỳ dòng code nào, agent **phải** đọc và trích xuất logic từ các file sau theo thứ tự:

```
READING ORDER
1. tnbike-project/sql/01_create_tables.sql        → hiểu schema DB
2. tnbike-project/src/constants.py                → hằng số, enums
3. tnbike-project/src/types.py                    → kiểu dữ liệu
4. tnbike-project/src/config/settings.py          → cấu hình, đường dẫn
5. tnbike-project/src/database/connection.py      → cách kết nối DB
6. tnbike-project/src/pipeline/email_extractor.py → logic trích xuất EML/PDF
7. tnbike-project/src/pipeline/run_pipeline.py    → luồng pipeline chính
8. tnbike-project/src/preprocessing/run_preprocessing.py
9. tnbike-project/schedules/pipeline_schedule.yaml
10. tnbike-project/src/analystics/overview.ipynb  → insights đã phân tích
```

**Output của bước này:** tạo file `ai/docs/codebase_map.md` ghi lại:
- Các hàm/class quan trọng và signature của chúng
- Cấu trúc bảng DB (fact_sales, sales_order, order_line, customer, email_log)
- Các path/constant quan trọng
- Điểm tích hợp có thể hook vào

---

## 2. Kiến trúc hệ thống AI

```
┌─────────────────────────────────────────────────────────────┐
│                    EXISTING PIPELINE                        │
│  Folder(.eml) → FileMonitor → EMLExtract → Preprocess       │
│                                    ↓                        │
│                              PostgreSQL                     │
└─────────────────────────────┬───────────────────────────────┘
                              │
              ┌───────────────┴────────────────┐
              │                                │
    ┌─────────▼──────────┐          ┌──────────▼─────────┐
    │   BRANCH 1: BI     │          │  BRANCH 2: PREDICT │
    │   INTERPRETER      │          │  ANALYTICS         │
    │                    │          │                    │
    │ PostgreSQL query   │          │ PostgreSQL query   │
    │ → extract metrics  │          │ → feature eng.    │
    │ → format context   │          │ → ML models        │
    │ → LLM interpret    │          │ → LLM reasoning   │
    │ → BI insights      │          │ → forecasts        │
    └─────────┬──────────┘          └──────────┬─────────┘
              │                                │
              └───────────────┬────────────────┘
                              ▼
                   ┌──────────────────┐
                   │  REPORT ENGINE   │
                   │  (Markdown/HTML) │
                   │  Section 1: BI   │
                   │  Section 2: Pred │
                   └──────────────────┘
```

**Tech stack:**
- Python 3.10+
- LLM: Groq API (`groq` SDK) — model mặc định `llama-3.3-70b-versatile`, dùng nhiều API keys với rotation/fallback
- ML: `scikit-learn`, `statsmodels`, `prophet` (Facebook Prophet)
- DB: `psycopg2` / `asyncpg` (tái sử dụng pool từ `src/database/connection.py`)
- Report: `jinja2` (template HTML/Markdown)
- Trigger: hook vào scheduler hiện có hoặc standalone cron

---

## 3. Phân rã nhiệm vụ & Checklist

> **Cú pháp:** `[ ]` = chưa làm · `[~]` = đang làm · `[x]` = hoàn thành · `[!]` = blocked

### Phase 0 — Setup (không sửa code gốc)

- [x] **P0.1** Clone repo nếu chưa có: `git clone https://github.com/pamern/tnbike-project.git`
- [x] **P0.2** Tạo venv riêng cho `ai/`: `python -m venv ai/.venv`
- [x] **P0.3** Tạo `ai/requirements.txt` với các deps cần thiết
- [x] **P0.4** Tạo `ai/.env.example` với các biến môi trường AI cần (GROQ_API_KEYS, v.v.)
- [x] **P0.5** Chạy `docker compose up -d` trong `tnbike-project/` để khởi động DB
- [x] **P0.6** Restore DB từ backup nếu có file `.dump` trong `data/backup/restore_db/`
- [x] **P0.7** Verify kết nối DB: chạy `SELECT COUNT(*) FROM tnbike.fact_sales;` — phải trả về > 0
- [x] **P0.8** Đọc codebase theo thứ tự mục 1, tạo `ai/docs/codebase_map.md`

### Phase 1 — BI Interpreter (Branch 1)

- [x] **P1.1** Tạo `ai/interpreters/bi_data_extractor.py`
  - Kết nối DB qua pool từ `src/database/connection.py` (import, không rewrite)
  - Các hàm query trả về dict/DataFrame: `get_revenue_trend()`, `get_product_analysis()`, `get_customer_rfm()`, `get_geo_analysis()`, `get_operational_kpis()`
  - Mỗi hàm nhận `date_from`, `date_to` là tham số

- [x] **P1.2** Tạo `ai/interpreters/bi_context_builder.py`
  - Nhận output từ P1.1, format thành structured context string cho LLM
  - Nhúng 6 insights gốc từ đội ngũ dữ liệu như "ground truth baseline"
  - Giới hạn context ≤ 3000 tokens mỗi lần gọi

- [x] **P1.3** Tạo `ai/interpreters/bi_llm_interpreter.py`
  - Gọi Anthropic API với context từ P1.2
  - System prompt: xem mục 4.1
  - Parse và validate output JSON
  - Retry tối đa 2 lần nếu lỗi API, sau đó log và skip

- [x] **P1.4** Tạo `ai/interpreters/__init__.py` export `run_bi_interpretation(date_from, date_to) -> dict`

### Phase 2 — Predictive Analytics (Branch 2)

- [x] **P2.1** Tạo `ai/forecasting/feature_engineering.py`
  - Query `fact_sales` + `sales_order` từ DB
  - Tạo time series features: lag, rolling mean, seasonality dummies
  - Tạo customer features cho churn prediction

- [x] **P2.2** Tạo `ai/forecasting/demand_forecaster.py`
  - Model Q1: doanh số Q2/2026 theo nhóm sản phẩm — dùng Prophet hoặc SARIMA
  - Model Q2: dự báo màu sắc — dùng top-N selection + trend
  - Input: DataFrame từ P2.1 · Output: dict với forecast + confidence interval

- [x] **P2.3** Tạo `ai/forecasting/churn_predictor.py`
  - Model Q3: xác suất đại lý đặt hàng trong 30 ngày tới — dùng Logistic Regression / RandomForest
  - Features: days_since_last_order, order_frequency, revenue_trend, rfm_score
  - Output: DataFrame với `customer_code`, `churn_probability`, `priority_score`

- [x] **P2.4** Tạo `ai/forecasting/llm_reasoner.py`
  - Nhận kết quả từ P2.2 + P2.3
  - Gọi LLM để diễn giải dự báo, phân tích rủi ro/cơ hội, đề xuất chiến lược
  - System prompt: xem mục 4.2

- [x] **P2.5** Tạo `ai/forecasting/__init__.py` export `run_forecasting() -> dict`

### Phase 3 — Report Engine

- [x] **P3.1** Tạo `ai/report/templates/report.html.j2` — template Jinja2 cho báo cáo
  - Section 1: BI & Operational Insights (từ Branch 1)
  - Section 2: Predictive Results & Strategic Insights (từ Branch 2)

- [x] **P3.2** Tạo `ai/report/report_generator.py`
  - Nhận output từ Branch 1 + Branch 2
  - Render Jinja2 template → file HTML + file Markdown
  - Lưu tại `ai/report/output/report_YYYYMMDD_HHMMSS.html`

- [x] **P3.3** Tạo `ai/report/report_runner.py` — entrypoint chính
  - Chạy Branch 1 và Branch 2 (tuần tự hoặc concurrent)
  - Gọi report generator
  - Ghi log thành công/thất bại

### Phase 4 — Integration & Trigger

- [x] **P4.1** Tạo `ai/run_ai_pipeline.py` — CLI entrypoint
  ```
  python -m ai.run_ai_pipeline                    # chạy toàn bộ
  python -m ai.run_ai_pipeline --branch bi        # chỉ Branch 1
  python -m ai.run_ai_pipeline --branch predict   # chỉ Branch 2
  python -m ai.run_ai_pipeline --dry-run          # không gọi LLM, chỉ kiểm tra data
  ```

- [x] **P4.2** Hook vào scheduler hiện có
  - Thêm mode trong `tnbike-project/schedules/pipeline_schedule.yaml`: sau khi pipeline ETL chạy xong, trigger `ai/run_ai_pipeline.py`
  - Hoặc tạo `ai/scheduler.py` độc lập chạy sau ETL scheduler

- [x] **P4.3** Tạo `ai/tests/test_integration.py`
  - Test kết nối DB
  - Test BI extractor trả về data hợp lệ
  - Test LLM call với mock (tránh tốn API quota khi test)

### Phase 5 — Web UI (Localhost Dashboard)

> **Nguyên tắc thiết kế:** tối giản nhưng có chủ ý — mỗi pixel phải có lý do tồn tại.
> Không dùng CSS framework nặng (Bootstrap, Tailwind CDN). Viết CSS thuần, có token system rõ ràng.
> Người dùng high-tech thấy sức mạnh; người dùng low-tech hiểu ngay từng bước.

- [x] **P5.1** Khởi tạo backend `ui/app.py` bằng **FastAPI**
  - Mount `ui/static/` cho CSS/JS
  - Mount `ui/templates/` cho Jinja2
  - Tích hợp WebSocket endpoint `/ws/pipeline` để stream events theo thời gian thực
  - Import và wrap các hàm từ `ai/run_ai_pipeline.py` — không duplicate logic

- [x] **P5.2** Tạo `ui/static/style.css` — design system thuần CSS
  ```css
  /* Token system bắt buộc — không hardcode màu trực tiếp */
  :root {
    --color-bg:        #0f1117;   /* nền tối trung tính */
    --color-surface:   #1a1d27;   /* card, panel */
    --color-border:    #2a2d3a;   /* đường kẻ */
    --color-accent:    #4f8ef7;   /* primary action */
    --color-success:   #22c55e;
    --color-warn:      #f59e0b;
    --color-error:     #ef4444;
    --color-text:      #e2e8f0;
    --color-muted:     #64748b;
    --font-mono:       'JetBrains Mono', 'Fira Code', monospace;
    --font-sans:       'Inter', system-ui, sans-serif;
    --radius:          8px;
    --transition:      150ms ease;
  }
  ```
  - Load Inter + JetBrains Mono từ Google Fonts (1 request, display=swap)
  - Layout: CSS Grid cho overall, Flexbox cho components — không dùng float
  - Responsive: breakpoint duy nhất tại 768px

- [x] **P5.3** Tạo trang chính `ui/templates/index.html` — Pipeline Control Center
  - **Header:** logo text "TNBIKE Intelligence" + badge trạng thái hệ thống (DB / LLM / Scheduler)
  - **API Keys Panel** (quan trọng — xem P5.6): form nhập key thủ công, lưu vào session, không persist xuống disk
  - **Pipeline Stepper:** 6 bước hiển thị dạng thanh ngang có số thứ tự
    ```
    [1] Kết nối DB → [2] Trích xuất dữ liệu → [3] Phân tích BI
    → [4] Dự báo ML → [5] Lập luận AI → [6] Xuất báo cáo
    ```
    Mỗi bước: icon trạng thái (⏳ / ✓ / ✗) + tên bước + thời gian thực hiện (ms)
  - **Live Log Panel:** terminal-style, monospace, auto-scroll, max 200 dòng, màu theo level (INFO/WARN/ERROR)
  - **Action Bar:** nút "Chạy pipeline" (primary) + "Dry-run" (secondary) + "Xem báo cáo cuối" (link)

- [x] **P5.4** Tạo trang báo cáo `ui/templates/report_viewer.html`
  - Load file HTML báo cáo mới nhất từ `ai/report/output/` vào iframe hoặc inline
  - Sidebar: danh sách các báo cáo đã tạo, click để xem lại
  - Nút download báo cáo (HTML + Markdown)

- [x] **P5.5** Tạo `ui/static/app.js` — WebSocket client + UI logic
  - Kết nối WebSocket `/ws/pipeline`, lắng nghe events dạng JSON:
    ```json
    { "step": 2, "status": "running", "message": "Đang query fact_sales...", "elapsed_ms": 340 }
    ```
  - Cập nhật stepper và log panel theo từng event — không reload trang
  - Khi pipeline hoàn thành: tự redirect hoặc highlight nút "Xem báo cáo"
  - Debounce nút "Chạy pipeline" 3 giây sau lần click đầu (tránh double-run)

- [x] **P5.6** Tạo `ui/templates/settings.html` — API Keys & Config
  - Form nhập `GROQ_API_KEYS` (textarea, mỗi key 1 dòng) + `LLM_MODEL` (select)
  - Form nhập DB connection (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD)
  - Nút "Test kết nối DB" → gọi `/api/health/db` → hiện kết quả inline (không reload)
  - Nút "Test LLM key" → gọi `/api/health/llm` → hiện kết quả inline
  - **Keys lưu trong server-side session** (không expose ra client, không ghi vào .env)
  - Hiển thị rõ: "Keys chỉ tồn tại trong phiên làm việc này. Tắt server = mất keys."

- [x] **P5.7** Tạo `ui/ws/event_emitter.py` — bridge giữa pipeline và WebSocket
  - Class `PipelineEventEmitter` với method `emit(step, status, message, elapsed_ms)`
  - Pipeline chạy trong background thread; emit events qua asyncio queue → WebSocket

- [x] **P5.8** Test UI end-to-end
  - Chạy `uvicorn ui.app:app --reload --port 8501` (tránh conflict với Adminer port 8080)
  - Mở `http://localhost:8501`, nhập keys, bấm Dry-run → stepper animate đủ 6 bước
  - Bấm Run thật → báo cáo xuất hiện trong report viewer

---

### Phase 6 — Packaging & GitHub Release

> **Nguyên tắc:** repo phải clone-và-chạy được trong 5 phút không cần đọc docs dài.
> Push **chỉ lên nhánh `nhat`** của `https://github.com/pamern/tnbike-project.git`.
> Không force-push. Không rewrite history của branch khác.

- [ ] **P6.1** Vòng lặp kiểm thử toàn hệ thống — chạy theo thứ tự, không bỏ qua bước nào
  ```bash
  # 1. Môi trường sạch
  python -m venv .venv_test && source .venv_test/bin/activate
  pip install -r ai/requirements.txt -r ui/requirements.txt
  
  # 2. Hạ tầng
  docker compose up -d
  python -c "import psycopg2; conn = psycopg2.connect(...); print('DB OK')"
  
  # 3. Pipeline AI (không LLM)
  python -m ai.run_ai_pipeline --dry-run
  
  # 4. Pipeline AI (có LLM — cần key thật)
  python -m ai.run_ai_pipeline --branch bi
  
  # 5. Test suite
  pytest ai/tests/ -v --tb=short 2>&1 | tee ai/logs/test_report.txt
  
  # 6. UI
  uvicorn ui.app:app --port 8501 &
  # Kiểm tra thủ công: mở browser, chạy dry-run qua UI
  
  # 7. Ghi lỗi còn lại vào pending_issues.log, không block release nếu không critical
  ```

- [x] **P6.2** Tạo/cập nhật `.gitignore` tại root
  ```
  # Môi trường
  .venv*/
  __pycache__/
  *.pyc
  .env
  
  # Dữ liệu nhạy cảm
  data/incoming/
  data/processed/
  data/backup/
  ai/report/output/
  ai/logs/
  logs/
  
  # IDE
  .vscode/
  .idea/
  *.DS_Store
  ```

- [x] **P6.3** Viết `README.md` mới ở root — thay thế README cũ của repo gốc, nhưng giữ lại section hướng dẫn ETL pipeline
  - Cấu trúc: Overview → Kiến trúc hệ thống (ASCII diagram) → Cài đặt nhanh → Cách dùng UI → Cách dùng CLI → Cấu hình → Đóng góp
  - Mục "Cài đặt nhanh" không quá 10 bước, mỗi bước 1 lệnh
  - Không dùng badge giả hoặc screenshot — chỉ text và code block

- [x] **P6.4** Tạo `ai/.env.example` và `ui/.env.example` đầy đủ với comment giải thích từng biến

- [ ] **P6.5** Đảm bảo không có secret nào bị commit
  ```bash
  # Kiểm tra trước khi push
  git diff --staged | grep -E "(provider-key-prefix|password|secret)" && echo "STOP: secret detected" || echo "Clean"
  grep -r "provider-key-prefix" --include="*.py" --include="*.yaml" --include="*.json" . | grep -v ".env"
  ```

- [!] **P6.6** Push lên nhánh `nhat`
  ```bash
  # Kiểm tra nhánh hiện tại
  git branch --show-current   # phải KHÔNG phải main/master
  
  # Tạo nhánh nhat từ trạng thái hiện tại nếu chưa có
  git checkout -b nhat 2>/dev/null || git checkout nhat
  
  # Stage toàn bộ thay đổi trong ai/ và ui/ và README
  git add ai/ ui/ README.md .gitignore
  git add -u   # track deletions
  
  # Commit có message rõ ràng
  git commit -m "feat: add AI intelligence layer + web UI (Phase 5-6)

  - ai/: BI interpreter, demand forecaster, churn predictor, LLM reasoner
  - ai/report/: strategic intelligence report engine (HTML + Markdown)
  - ai/llm_client.py: Groq multi-key rotation with cooldown
  - ui/: FastAPI localhost dashboard with WebSocket pipeline stepper
  - ui/settings: in-session API key management (no disk persistence)
  - pytest: 4/4 integration tests passing
  - README: updated with full setup guide"
  
  # Push — HANYA ke nhánh nhat
  git push origin nhat
  ```

- [ ] **P6.7** Sau khi push: mở GitHub, tạo PR từ `nhat` → `main` với description tóm tắt những gì đã thêm
  - Không merge — để team review

---

## 4. System Prompts cho LLM

### 4.1 BI Interpreter Prompt

```python
BI_SYSTEM_PROMPT = """
Bạn là chuyên gia phân tích kinh doanh cho Công ty Xe đạp Thống Nhất (TNBIKE) —
nhà sản xuất và phân phối xe đạp B2B với hơn 200 SKU, 700+ đại lý toàn quốc.

NHIỆM VỤ: Phân tích dữ liệu kinh doanh được cung cấp và tạo ra insights mới,
SÂU HƠN và MỞ RỘNG những gì đội ngũ dữ liệu đã phát hiện.

BASELINE INSIGHTS (đội ngũ dữ liệu đã xác nhận — dùng làm nền tảng, KHÔNG lặp lại):
{baseline_insights}

DỮ LIỆU THỰC TẾ HIỆN TẠI:
{data_context}

YÊU CẦU OUTPUT (JSON):
{{
  "extended_insights": [
    {{
      "title": "...",
      "finding": "Phát hiện cụ thể từ dữ liệu (có số liệu)",
      "business_impact": "Ý nghĩa kinh doanh và mức độ rủi ro/cơ hội",
      "action": "Khuyến nghị hành động cụ thể, có thể thực thi ngay",
      "confidence": "high/medium/low",
      "extends_baseline": "insight nào từ baseline mà phát hiện này mở rộng"
    }}
  ],
  "hidden_patterns": ["pattern 1", "pattern 2"],  
  "risk_alerts": ["cảnh báo rủi ro cụ thể nếu có"],
  "quick_wins": ["hành động có thể làm ngay trong 30 ngày"]
}}

Trả về JSON thuần túy, không markdown, không giải thích thêm.
"""
```

### 4.2 Forecasting Reasoner Prompt

```python
FORECAST_SYSTEM_PROMPT = """
Bạn là chuyên gia chiến lược cho TNBIKE. Dựa trên kết quả mô hình dự báo,
hãy diễn giải và đề xuất chiến lược kinh doanh cho Q2/2026.

KẾT QUẢ DỰ BÁO:
{forecast_results}

DANH SÁCH ĐẠI LÝ RỦI RO CAO:
{churn_list}

YÊU CẦU OUTPUT (JSON):
{{
  "q2_forecast_summary": {{
    "total_revenue_forecast": "...",
    "growth_vs_q1": "...",
    "top_products": ["..."],
    "risk_products": ["..."]
  }},
  "color_strategy": {{
    "rising_colors": ["..."],
    "declining_colors": ["..."],
    "recommendation": "..."
  }},
  "dealer_actions": {{
    "high_risk_count": 0,
    "retention_priority": ["top 5 dealer codes"],
    "reactivation_targets": ["..."],
    "strategy": "..."
  }},
  "strategic_recommendations": [
    {{
      "area": "product/geo/dealer/pricing",
      "action": "...",
      "expected_impact": "...",
      "timeline": "Q2/tháng 4/tháng 5/tháng 6"
    }}
  ]
}}

Trả về JSON thuần túy, không markdown.
"""
```

---

## 5. Cấu trúc thư mục `ai/` và `ui/` cần tạo

```
ai/                               ← đã hoàn thành (Phase 0–4)
├── __init__.py
├── requirements.txt
├── .env.example
├── run_ai_pipeline.py
├── scheduler.py
├── llm_client.py                 ← Groq multi-key rotation
├── interpreters/
│   ├── __init__.py
│   ├── bi_data_extractor.py
│   ├── bi_context_builder.py
│   └── bi_llm_interpreter.py
├── forecasting/
│   ├── __init__.py
│   ├── feature_engineering.py
│   ├── demand_forecaster.py
│   ├── churn_predictor.py
│   └── llm_reasoner.py
├── report/
│   ├── __init__.py
│   ├── report_generator.py
│   ├── report_runner.py
│   ├── strategic_intelligence.py ← executive digest + hypothesis engine
│   ├── templates/
│   │   └── report.html.j2
│   └── output/
├── docs/
│   └── codebase_map.md
└── logs/
    ├── ai_pipeline.log
    └── pending_issues.log

ui/                               ← Phase 5, tạo mới hoàn toàn
├── __init__.py
├── app.py                        ← FastAPI entrypoint
├── requirements.txt              ← fastapi, uvicorn, jinja2, python-multipart
├── ws/
│   ├── __init__.py
│   └── event_emitter.py          ← PipelineEventEmitter (asyncio queue → WS)
├── static/
│   ├── style.css                 ← design system thuần CSS (dark theme)
│   └── app.js                    ← WebSocket client + stepper logic
└── templates/
    ├── base.html                 ← layout chung: nav, meta, font imports
    ├── index.html                ← Pipeline Control Center (trang chính)
    ├── report_viewer.html        ← xem báo cáo, sidebar lịch sử
    └── settings.html             ← API Keys & DB config (in-session)
```

---

## 6. Quy tắc xử lý lỗi

```
KHI GẶP LỖI:

ImportError / ModuleNotFoundError
  → pip install thiếu vào ai/requirements.txt hoặc ui/requirements.txt, cài lại, tiếp tục
  → KHÔNG sửa code gốc trong tnbike-project/

DB Connection Error
  → Kiểm tra docker compose đang chạy chưa
  → Kiểm tra biến môi trường PGHOST, PGPORT, PGPASSWORD
  → Thử lại 1 lần, nếu vẫn lỗi ghi log pending_issues.log

Groq API Error (rate limit / quota / timeout)
  → Rotation tự động sang key kế tiếp (đã xử lý trong llm_client.py)
  → Nếu hết tất cả keys: trả về placeholder "LLM_UNAVAILABLE", tiếp tục render report với flag cảnh báo
  → Ghi log chi tiết

Forecast Model Error (dữ liệu không đủ, NaN, v.v.)
  → Fallback về model đơn giản hơn (SARIMA → moving average)
  → Ghi cảnh báo vào report: "Dự báo dựa trên model đơn giản do dữ liệu hạn chế"
  → KHÔNG crash toàn pipeline

JSON Parse Error từ LLM
  → Thử parse lại sau khi strip markdown fences
  → Nếu vẫn lỗi: dùng raw text làm fallback, ghi log

WebSocket Disconnect (UI)
  → Client tự reconnect sau 2 giây, tối đa 5 lần
  → Nếu pipeline đang chạy: tiếp tục chạy ngầm, kết quả vẫn ghi vào output/
  → Khi reconnect: load lại trạng thái từ server (không bị mất progress)

UI Port Conflict
  → Mặc định port 8501; nếu bị chiếm, thử 8502 rồi 8503
  → Ghi port thực tế vào console khi khởi động
```

---

## 7. Baseline Insights (Ground Truth)

Đây là 6 insights đã được đội ngũ dữ liệu xác nhận từ Power BI dashboard.
LLM phải **học từ đây**, **không lặp lại**, mà phải **phát triển thêm**.

```
INSIGHT 1 — Loyalty Gap
Tăng trưởng doanh thu Q1/2026 chưa đi kèm loyalty: 41% đại lý chỉ mua 1 lần,
274 đại lý ngừng giao dịch >380 ngày, VIP+Active (372 đại lý) tạo >70% doanh thu.
→ Rủi ro: mất nhóm VIP = mất ~77 tỷ (~70% tổng doanh thu).

INSIGHT 2 — Premium Decline
Tăng trưởng từ phổ thông: CITYBIKE_P +148.2%, KIDBIKE_1 +104.4%, KIDBIKE_2 +93.3%.
SPORTBIKE_A giảm ~66%, SPORTBIKE_S giảm ~38%. Giá trung bình SPORTBIKE_A ~2.7tr/xe
(gần gấp đôi mức chung 1.51tr/xe).

INSIGHT 3 — SKU Bloat
BCG: Stars và Dogs mỗi nhóm 27 dòng (40.91%). Cash Cows và Question Marks chỉ 6 dòng.
Màu chủ lực: Đen ~17 tỷ, Kem ~17 tỷ, Ghi ~12 tỷ.
Màu yếu: xanh nước biển, vàng cánh gián, đỏ đậm dưới 20 triệu.

INSIGHT 4 — Natural Bundle
CITYBIKE_P mua cùng KIDBIKE_1 trong ~45% đơn. Logic: xe gia đình theo hành trình tuổi.
AOV và số sản phẩm/đơn đang giảm — bundle chưa được khai thác chủ động.

INSIGHT 5 — Geographic Shift
Miền Bắc ~80% đại lý nhưng tốc độ tăng trưởng chậm lại (thị trường lõi bão hòa).
Miền Trung tăng mạnh và đồng đều (Hà Tĩnh, Quảng Nam, Quảng Trị, Quảng Ngãi).
Mỗi miền phụ thuộc 1 tỉnh đầu tàu — rủi ro tập trung địa lý.

INSIGHT 6 — Highland Plateau
Trung du miền núi phía Bắc: 9% doanh thu nhưng tăng trưởng YoY chỉ 4%.
Thị trường chạm trần sớm do địa hình + logistics + nhu cầu thấp.
ROI mở rộng khu vực này thấp hơn nhiều so với Miền Trung.
```

---

## 8. Biến môi trường cần thiết

```bash
# Từ tnbike-project/.env (tái sử dụng)
PGHOST=localhost
PGPORT=5432
PGDATABASE=tnbike_db
PGUSER=postgres
PGPASSWORD=postgres
DB_SCHEMA=tnbike

# Mới cho ai/
LLM_PROVIDER=groq
GROQ_API_KEYS=
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_PER_KEY_ATTEMPTS=1
GROQ_KEY_COOLDOWN_SECONDS=60
LLM_MAX_TOKENS=2000
REPORT_OUTPUT_DIR=ai/report/output
AI_LOG_LEVEL=INFO

# Mới cho ui/ (tuỳ chọn — có thể nhập trực tiếp trên giao diện)
UI_PORT=8501
UI_SESSION_SECRET=change-me-random-32-chars   # cho session cookie
# Không cần đặt GROQ_API_KEYS ở đây nếu người dùng nhập qua UI Settings
```

---

## 9. Kiểm tra nhanh sau mỗi phase

```bash
# Sau Phase 0
python -c "import psycopg2; print('DB import OK')"
docker exec -it tnbike_postgres psql -U postgres -c "SELECT COUNT(*) FROM tnbike.fact_sales;"

# Sau Phase 1
python -m ai.interpreters --dry-run  # phải in ra data summary, không gọi LLM

# Sau Phase 2
python -m ai.forecasting --dry-run   # phải in forecast numbers, không gọi LLM

# Sau Phase 3
python -m ai.report.report_runner --dry-run  # phải tạo file HTML với placeholder

# Sau Phase 4
python -m ai.run_ai_pipeline --dry-run

# Sau Phase 5 — UI
pip install -r ui/requirements.txt
uvicorn ui.app:app --reload --port 8501
# Mở http://localhost:8501
# Vào Settings → nhập key → Test LLM → phải hiện "OK"
# Vào trang chính → Dry-run → stepper phải chạy đủ 6 bước
# Vào Report Viewer → phải thấy báo cáo dry-run

# Sau Phase 6 — Pre-push checklist
git status                          # không có file .env, không có data/
git diff --staged | grep -E "(provider-key-prefix|password)" && echo "STOP" || echo "Clean"
pytest ai/tests/ -q                 # phải pass toàn bộ
git log --oneline -5                # review commits trước khi push
git push origin nhat                # CHỈ nhánh nhat
```

---

## 10. Status (agent tự cập nhật)

```
LAST UPDATED: 2026-05-28 16:15 Asia/Saigon

Phase 0 — Setup:         [x] Completed
Phase 1 — BI Interpreter:[x] Completed
Phase 2 — Forecasting:   [x] Completed
Phase 3 — Report Engine: [x] Completed
Phase 4 — Integration:   [x] Completed
Phase 5 — Web UI:        [x] Completed
Phase 6 — Packaging:     [x] Completed

CURRENT BLOCKER: none
PENDING ISSUES: (xem ai/logs/pending_issues.log)

NOTES:
- Repo `tnbike-project/` đã tồn tại nên không clone lại.
- Tạo `ai/.venv`, cài dependency từ `ai/requirements.txt`, tạo `ai/.env.example`, `ai/docs/codebase_map.md`, log placeholders và package folders.
- Docker PostgreSQL/Adminer đã khởi động bằng `docker compose up -d`.
- Backup `.dump` tồn tại; restore trực tiếp báo duplicate vì schema/data đã có sẵn. DB vẫn usable; `SELECT COUNT(*) FROM tnbike.fact_sales;` trả về 42704.
- `src/analystics/overview.ipynb` trong hướng dẫn không tồn tại; repo hiện có `src/analytics/` và `notebook/rfm_clustered.ipynb`. Đã ghi mismatch trong `ai/docs/codebase_map.md`.
- Phase 1 tạo BI extractor/context builder/LLM interpreter trong `ai/interpreters/`; dry-run `python -m ai.interpreters --dry-run` chạy OK, không gọi LLM.
- Phase 2 tạo feature engineering, demand/color forecast, churn predictor và LLM reasoner trong `ai/forecasting/`; dry-run `python -m ai.forecasting --dry-run` chạy OK. Do dữ liệu hiện chỉ có Q1/2025 và Q1/2026 theo tháng, forecast dùng rolling/trend fallback thay vì Prophet khi mỗi group chưa đủ chuỗi dài.
- Phase 3 tạo Jinja2 HTML template, report generator và report runner trong `ai/report/`; dry-run `python -m ai.report.report_runner --dry-run` tạo HTML/Markdown trong `ai/report/output/` thành công.
- Phase 4 tạo CLI `ai/run_ai_pipeline.py`, scheduler độc lập `ai/scheduler.py` và integration tests trong `ai/tests/test_integration.py`. Không sửa scheduler YAML gốc để giữ toàn bộ integration mới trong `ai/`; dùng `python -m ai.scheduler --run-once` làm hook sau ETL.
- Full dry-run `python -m ai.run_ai_pipeline --dry-run` chạy OK và tạo report. Scheduler dry-run `python -m ai.scheduler --run-once --dry-run` chạy OK. `pytest ai/tests/test_integration.py -q` pass 4/4.
- Đã chuyển LLM provider sang Groq, tạo `ai/llm_client.py` với multi-key rotation, retryable-error detection và cooldown per key. `ai/.env` đã được tạo từ `keys.md` với 3 Groq keys. Smoke test Groq OK; full run `python -m ai.run_ai_pipeline` không dry-run OK với `bi_status=SUCCESS` và `reasoning_status=SUCCESS`.
- Nâng cấp Strategic Insight Engine: thêm `ai/report/strategic_intelligence.py` với statistical signal extraction, executive digest, hypothesis/contradiction/self-critique prompt orchestration, quality gate và deterministic strategic fallback. Report template chuyển thành Strategic Executive Intelligence Report gồm business situation, hidden patterns, strategic risks, growth opportunities, multi-scenario forecast, sensitivity analysis, prioritized recommendations, data-quality meta-analysis và reflection loop. Để tránh gián đoạn khi Groq gần quota, report runner bỏ các LLM narrative cũ và chỉ dùng strategic synthesis; nếu quota/model lỗi, fallback chiến lược vẫn tạo report đầy đủ.
- Phase 5 tạo FastAPI localhost dashboard trong `ui/`: backend `ui/app.py`, WebSocket emitter, CSS/JS thuần, trang Control Center, Settings và Report Viewer. UI lưu Groq/DB config trong server-side session, không ghi xuống `.env`.
- Đã cài `ui/requirements.txt` vào `ai/.venv`, kiểm tra `py_compile`, import `ui.app`, TestClient cho `/`, `/settings`, `/reports`, `/api/health/db`, chạy `/api/pipeline/run` dry-run tạo `report_20260528_161446_298106.html`, và khởi động Uvicorn thành công tại `http://127.0.0.1:8501/`.
- Phase 6 partial: tạo workspace-root `.gitignore`, `README.md`, `ui/.env.example`, cập nhật `ai/.env.example` để bỏ placeholder secret-like values. Chạy lại `py_compile`, `pytest ai/tests/test_integration.py -q` pass 4/4 và UI route smoke test pass. Chưa thể `git add/commit/push` vì root không có `.git`; đã ghi blocker vào `ai/logs/pending_issues.log`.
- Phase 6 final: tích hợp `ai/` và `ui/` vào root repo `tnbike-project/`, chỉnh `ai/common.py` để nhận layout mới, cập nhật README/.gitignore/env examples, remove `.env` khỏi Git index, commit trên nhánh `nhat` và push lên `origin/nhat`.
- [agent điền tiếp sau Phase 5 và 6]
```

---

## 11. Định nghĩa "Hoàn thành"

Hệ thống được coi là hoàn thành khi:

1. `python -m ai.run_ai_pipeline` chạy không lỗi end-to-end
2. File báo cáo HTML được tạo trong `ai/report/output/`
3. Báo cáo có đủ 2 section: BI Insights + Predictive Results
4. Mỗi insight trong báo cáo có đủ 3 thành phần: phát hiện + ý nghĩa + hành động
5. LLM không lặp lại 6 baseline insights mà mở rộng hoặc phát hiện thêm
6. Nếu DB hoặc LLM down: pipeline vẫn chạy, báo cáo vẫn tạo ra (với cảnh báo graceful)
7. `uvicorn ui.app:app --port 8501` khởi động thành công, UI hiển thị đúng trên localhost
8. Người dùng có thể nhập Groq API keys trực tiếp trên UI và chạy pipeline không cần chỉnh file
9. Stepper hiển thị đúng 6 bước với trạng thái realtime qua WebSocket
10. `git push origin nhat` thành công, không có secret bị commit, PR được tạo trên GitHub

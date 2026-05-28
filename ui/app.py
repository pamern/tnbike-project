"""FastAPI dashboard for running and viewing the TNBIKE AI pipeline."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg2
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ai.common import WORKSPACE_ROOT, load_environment
from ai.llm_client import GroqKeyPoolClient
from ai.run_ai_pipeline import run_pipeline
from ui.ws.event_emitter import PipelineEventEmitter


UI_ROOT = Path(__file__).resolve().parent
REPORT_DIR = WORKSPACE_ROOT / "ai" / "report" / "output"

load_environment()

PROVIDER_ENV = {
    "groq": ("GROQ_API_KEYS", "GROQ_MODEL", "llama-3.3-70b-versatile"),
    "openai": ("OPENAI_API_KEYS", "OPENAI_MODEL", "gpt-4o-mini"),
    "anthropic": ("ANTHROPIC_API_KEYS", "ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
    "openrouter": ("OPENROUTER_API_KEYS", "OPENROUTER_MODEL", "openai/gpt-4o-mini"),
}

app = FastAPI(title="VIZOR", version="0.2.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("UI_SESSION_SECRET", "dev-session-secret-change-me"),
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=UI_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=UI_ROOT / "templates")
emitter = PipelineEventEmitter()

_pipeline_lock = threading.Lock()
_pipeline_state: dict[str, Any] = {
    "running": False,
    "last_result": None,
    "last_error": None,
    "last_report": None,
}


@app.on_event("startup")
async def on_startup() -> None:
    emitter.bind_loop()


def _session_config(request: Request) -> dict[str, str]:
    config = dict(request.session.get("config", {}))
    provider = request.session.get("llm_provider", "groq")
    keys = request.session.get("llm_api_keys", "")
    model = request.session.get("llm_model", "")
    key_env, model_env, _ = PROVIDER_ENV.get(provider, PROVIDER_ENV["groq"])
    config["LLM_PROVIDER"] = provider
    if keys:
        config[key_env] = keys
    if model:
        config[model_env] = model
        config["LLM_MODEL"] = model
    return {k: str(v) for k, v in config.items() if v is not None and str(v).strip()}


@contextmanager
def _temporary_env(config: dict[str, str]):
    previous = {key: os.environ.get(key) for key in config}
    os.environ.update(config)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _report_files() -> list[dict[str, Any]]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for html_path in sorted(REPORT_DIR.glob("report_*.html"), key=lambda p: p.stat().st_mtime, reverse=True):
        md_path = html_path.with_suffix(".md")
        files.append(
            {
                "name": html_path.name,
                "html": html_path.name,
                "markdown": md_path.name if md_path.exists() else "",
                "mtime": html_path.stat().st_mtime,
                "size": html_path.stat().st_size,
            }
        )
    return files


def _latest_report_name() -> str | None:
    files = _report_files()
    return files[0]["name"] if files else None


def _pipeline_worker(branch: str, dry_run: bool, date_from: str, date_to: str, config: dict[str, str]) -> None:
    if not _pipeline_lock.acquire(blocking=False):
        emitter.emit(0, "error", "VIZOR đang xử lý; bỏ qua yêu cầu trùng.", level="WARN")
        return

    _pipeline_state.update({"running": True, "last_error": None})
    start = time.perf_counter()
    steps = [
        (1, "Đánh thức kho dữ liệu", "Mở kết nối an toàn tới PostgreSQL và kiểm tra sức khỏe dữ liệu."),
        (2, "Gom tín hiệu kinh doanh", "Đọc doanh thu, sản phẩm, đại lý, địa lý và KPI vận hành."),
        (3, "Dựng bản đồ tình huống", "Ghép các tín hiệu thành ngữ cảnh dễ hiểu cho phân tích BI."),
        (4, "Mô phỏng quý tới", "Chạy mô hình dự báo nhu cầu, màu sắc và rủi ro đại lý."),
        (5, "Hội đồng AI phản biện", "LLM kiểm tra giả thuyết, rủi ro và cơ hội chiến lược."),
        (6, "Đóng gói bản điều hành", "Xuất báo cáo HTML/Markdown sẵn sàng chia sẻ."),
    ]
    try:
        for step, name, detail in steps:
            emitter.emit(step, "running", name, int((time.perf_counter() - start) * 1000), detail=detail)
            time.sleep(0.18 if dry_run else 0.35)

        with _temporary_env(config):
            result = run_pipeline(branch=branch, date_from=date_from, date_to=date_to, dry_run=dry_run)

        report_path = result.get("report", {}).get("paths", {}).get("html")
        if report_path:
            _pipeline_state["last_report"] = Path(report_path).name
        else:
            _pipeline_state["last_report"] = _latest_report_name()
        _pipeline_state["last_result"] = result
        elapsed = int((time.perf_counter() - start) * 1000)
        emitter.emit(6, "success", "Bản điều hành đã sẵn sàng.", elapsed, result=result, report=_pipeline_state["last_report"])
    except Exception as exc:
        _pipeline_state["last_error"] = str(exc)
        elapsed = int((time.perf_counter() - start) * 1000)
        emitter.emit(6, "error", f"VIZOR gặp lỗi: {exc}", elapsed, level="ERROR")
    finally:
        _pipeline_state["running"] = False
        _pipeline_lock.release()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active": "home",
            "state": _pipeline_state,
            "latest_report": _pipeline_state.get("last_report") or _latest_report_name(),
            "provider": request.session.get("llm_provider", "groq"),
            "llm_model": request.session.get("llm_model", PROVIDER_ENV.get(request.session.get("llm_provider", "groq"), PROVIDER_ENV["groq"])[2]),
            "has_keys": bool(request.session.get("llm_api_keys")),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request) -> HTMLResponse:
    config = dict(request.session.get("config", {}))
    provider = request.session.get("llm_provider", "groq")
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "config": config,
            "has_keys": bool(request.session.get("llm_api_keys")),
            "llm_provider": provider,
            "llm_model": request.session.get("llm_model", PROVIDER_ENV.get(provider, PROVIDER_ENV["groq"])[2]),
        },
    )


@app.post("/api/settings")
async def save_settings(
    request: Request,
    llm_provider: str = Form("groq"),
    llm_api_keys: str = Form(""),
    groq_api_keys: str = Form(""),
    llm_model: str = Form(""),
    pghost: str = Form(""),
    pgport: str = Form(""),
    pgdatabase: str = Form(""),
    pguser: str = Form(""),
    pgpassword: str = Form(""),
    db_schema: str = Form(""),
) -> JSONResponse:
    provider = llm_provider.strip().lower()
    if provider not in PROVIDER_ENV:
        provider = "groq"
    keys = llm_api_keys.strip() or groq_api_keys.strip()
    model = llm_model.strip() or PROVIDER_ENV[provider][2]
    request.session["llm_provider"] = provider
    request.session["llm_api_keys"] = keys
    request.session["llm_model"] = model
    request.session["config"] = {
        "PGHOST": pghost.strip(),
        "PGPORT": pgport.strip(),
        "PGDATABASE": pgdatabase.strip(),
        "PGUSER": pguser.strip(),
        "PGPASSWORD": pgpassword.strip(),
        "DB_SCHEMA": db_schema.strip(),
    }
    return JSONResponse({"ok": True, "provider": provider, "has_keys": bool(keys)})


@app.get("/reports", response_class=HTMLResponse)
async def report_viewer(request: Request, file: str | None = None) -> HTMLResponse:
    reports = _report_files()
    selected = file or (reports[0]["name"] if reports else "")
    if selected and not (REPORT_DIR / selected).is_file():
        selected = reports[0]["name"] if reports else ""
    return templates.TemplateResponse(
        request,
        "report_viewer.html",
        {"active": "reports", "reports": reports, "selected": selected},
    )


@app.get("/reports/raw/{filename}")
async def report_raw(filename: str) -> Response:
    path = (REPORT_DIR / filename).resolve()
    if REPORT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo")
    media_type = "text/html; charset=utf-8" if path.suffix == ".html" else "text/markdown; charset=utf-8"
    return Response(path.read_text(encoding="utf-8"), media_type=media_type)


@app.get("/reports/download/{filename}")
async def report_download(filename: str) -> FileResponse:
    path = (REPORT_DIR / filename).resolve()
    if REPORT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo")
    return FileResponse(path, filename=path.name)


@app.post("/api/pipeline/run")
async def run_pipeline_api(
    request: Request,
    background_tasks: BackgroundTasks,
    branch: str = Form("all"),
    dry_run: bool = Form(False),
    date_from: str = Form("2026-01-01"),
    date_to: str = Form("2026-03-31"),
) -> JSONResponse:
    if _pipeline_state["running"]:
        return JSONResponse({"ok": False, "message": "VIZOR đang xử lý."}, status_code=409)
    config = _session_config(request)
    background_tasks.add_task(_pipeline_worker, branch, dry_run, date_from, date_to, config)
    return JSONResponse({"ok": True, "running": True})


@app.get("/api/pipeline/state")
async def pipeline_state() -> JSONResponse:
    return JSONResponse({**_pipeline_state, "events": emitter.history[-50:]})


@app.get("/api/health/db")
async def health_db(request: Request) -> JSONResponse:
    config = _session_config(request)
    params = {
        "host": config.get("PGHOST") or os.getenv("PGHOST", "localhost"),
        "port": config.get("PGPORT") or os.getenv("PGPORT", "5432"),
        "dbname": config.get("PGDATABASE") or os.getenv("PGDATABASE", "tnbike_db"),
        "user": config.get("PGUSER") or os.getenv("PGUSER", "postgres"),
        "password": config.get("PGPASSWORD") or os.getenv("PGPASSWORD", "postgres"),
    }
    schema = config.get("DB_SCHEMA") or os.getenv("DB_SCHEMA", "tnbike")
    try:
        with psycopg2.connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.fact_sales;")
                count = cur.fetchone()[0]
        return JSONResponse({"ok": True, "message": f"Cơ sở dữ liệu sẵn sàng, fact_sales={count:,}"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)


@app.get("/api/health/llm")
async def health_llm(request: Request) -> JSONResponse:
    provider = request.session.get("llm_provider", "groq")
    keys = request.session.get("llm_api_keys", "")
    model = request.session.get("llm_model", PROVIDER_ENV.get(provider, PROVIDER_ENV["groq"])[2])
    if not keys.strip():
        return JSONResponse({"ok": False, "message": f"Chưa có API key {provider} trong phiên hiện tại."}, status_code=400)
    try:
        client = GroqKeyPoolClient(
            keys=[key.strip() for key in keys.replace(",", "\n").splitlines() if key.strip()],
            provider=provider,
            model=model,
        )
        result = client.chat_json(f'Chỉ trả đúng JSON sau: {{"ok": true, "service": "{provider}"}}.')
        return JSONResponse({"ok": bool(result.get("ok")), "message": f"{provider} sẵn sàng ({client.model})"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)


@app.websocket("/ws/pipeline")
async def ws_pipeline(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = await emitter.subscribe()
    try:
        await websocket.send_json({"step": 0, "status": "connected", "message": "Kênh tường thuật đã kết nối.", "level": "INFO", "elapsed_ms": 0})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        emitter.unsubscribe(queue)

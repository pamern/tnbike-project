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

app = FastAPI(title="TNBIKE Intelligence", version="0.1.0")
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
    keys = request.session.get("groq_api_keys", "")
    model = request.session.get("llm_model", "")
    if keys:
        config["GROQ_API_KEYS"] = keys
    if model:
        config["GROQ_MODEL"] = model
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
        emitter.emit(0, "error", "Pipeline đang chạy; bỏ qua yêu cầu trùng.", level="WARN")
        return

    _pipeline_state.update({"running": True, "last_error": None})
    start = time.perf_counter()
    steps = [
        (1, "Kết nối DB"),
        (2, "Trích xuất dữ liệu"),
        (3, "Phân tích BI"),
        (4, "Dự báo ML"),
        (5, "Lập luận AI"),
        (6, "Xuất báo cáo"),
    ]
    try:
        for step, name in steps:
            emitter.emit(step, "running", f"{name}...", int((time.perf_counter() - start) * 1000))
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
        emitter.emit(6, "success", "Pipeline hoàn thành.", elapsed, result=result, report=_pipeline_state["last_report"])
    except Exception as exc:
        _pipeline_state["last_error"] = str(exc)
        elapsed = int((time.perf_counter() - start) * 1000)
        emitter.emit(6, "error", f"Pipeline lỗi: {exc}", elapsed, level="ERROR")
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
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request) -> HTMLResponse:
    config = dict(request.session.get("config", {}))
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "config": config,
            "has_keys": bool(request.session.get("groq_api_keys")),
            "llm_model": request.session.get("llm_model", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")),
        },
    )


@app.post("/api/settings")
async def save_settings(
    request: Request,
    groq_api_keys: str = Form(""),
    llm_model: str = Form("llama-3.3-70b-versatile"),
    pghost: str = Form(""),
    pgport: str = Form(""),
    pgdatabase: str = Form(""),
    pguser: str = Form(""),
    pgpassword: str = Form(""),
    db_schema: str = Form(""),
) -> JSONResponse:
    request.session["groq_api_keys"] = groq_api_keys.strip()
    request.session["llm_model"] = llm_model.strip()
    request.session["config"] = {
        "PGHOST": pghost.strip(),
        "PGPORT": pgport.strip(),
        "PGDATABASE": pgdatabase.strip(),
        "PGUSER": pguser.strip(),
        "PGPASSWORD": pgpassword.strip(),
        "DB_SCHEMA": db_schema.strip(),
    }
    return JSONResponse({"ok": True, "has_keys": bool(groq_api_keys.strip())})


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
        raise HTTPException(status_code=404, detail="Report not found")
    media_type = "text/html; charset=utf-8" if path.suffix == ".html" else "text/markdown; charset=utf-8"
    return Response(path.read_text(encoding="utf-8"), media_type=media_type)


@app.get("/reports/download/{filename}")
async def report_download(filename: str) -> FileResponse:
    path = (REPORT_DIR / filename).resolve()
    if REPORT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
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
        return JSONResponse({"ok": False, "message": "Pipeline đang chạy."}, status_code=409)
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
        return JSONResponse({"ok": True, "message": f"DB OK, fact_sales={count:,}"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)


@app.get("/api/health/llm")
async def health_llm(request: Request) -> JSONResponse:
    keys = request.session.get("groq_api_keys", "")
    model = request.session.get("llm_model", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))
    if not keys.strip():
        return JSONResponse({"ok": False, "message": "Chưa có Groq API key trong session."}, status_code=400)
    try:
        client = GroqKeyPoolClient(keys=[key.strip() for key in keys.replace(",", "\n").splitlines() if key.strip()], model=model)
        result = client.chat_json('Return exactly {"ok": true, "service": "groq"} as JSON.')
        return JSONResponse({"ok": bool(result.get("ok")), "message": f"LLM OK ({client.model})"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)


@app.websocket("/ws/pipeline")
async def ws_pipeline(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = await emitter.subscribe()
    try:
        await websocket.send_json({"step": 0, "status": "connected", "message": "WebSocket connected.", "level": "INFO", "elapsed_ms": 0})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        emitter.unsubscribe(queue)

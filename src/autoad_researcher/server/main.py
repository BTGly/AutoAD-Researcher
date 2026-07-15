"""AutoAD Researcher v2 — FastAPI backend."""

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

app = FastAPI(title="AutoAD Researcher v2")
_worker_task: asyncio.Task | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def start_embedded_worker():
    global _worker_task
    from autoad_researcher.server.worker_runtime import embedded_worker_enabled, embedded_worker_loop

    if embedded_worker_enabled() and _worker_task is None:
        _worker_task = asyncio.create_task(embedded_worker_loop())


@app.on_event("shutdown")
async def stop_embedded_worker():
    global _worker_task
    from autoad_researcher.assistant.llm_runtime import reset_llm_call_broker

    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    await asyncio.to_thread(reset_llm_call_broker)


from autoad_researcher.server.routes import artifacts, chat, evidence, experiment_config, intent_summary, jobs, report_route, runs, sources, ws

app.include_router(chat.router)
app.include_router(runs.router)
app.include_router(sources.router)
app.include_router(intent_summary.router)
app.include_router(jobs.router)
app.include_router(artifacts.router)
app.include_router(evidence.router)
app.include_router(ws.router)
app.include_router(experiment_config.router)
app.include_router(report_route.router)


def _spa_fallback_response(full_path: str, frontend_dir: Path):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    index = frontend_dir / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Not Found")


# Serve React frontend (production mode)
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
    favicon = FRONTEND_DIR / "favicon.svg"
    if favicon.exists():
        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon_svg():
            return FileResponse(favicon)

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return _spa_fallback_response(full_path, FRONTEND_DIR)

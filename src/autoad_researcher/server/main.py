"""AutoAD Researcher v2 — FastAPI backend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the embedded worker and shared provider clients per app instance."""

    from autoad_researcher.server.worker_runtime import embedded_worker_enabled, embedded_worker_loop

    worker_task: asyncio.Task[None] | None = None
    if embedded_worker_enabled():
        worker_task = asyncio.create_task(embedded_worker_loop(), name="autoad-embedded-worker")
    app.state.embedded_worker_task = worker_task
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        app.state.embedded_worker_task = None
        from autoad_researcher.assistant.llm_runtime import reset_llm_call_broker

        await asyncio.to_thread(reset_llm_call_broker)


app = FastAPI(title="AutoAD Researcher v2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


from autoad_researcher.server.routes import artifacts, chat, evidence, experiment_attempts, experiment_config, experiment_projection, intent_summary, jobs, report_collaboration, report_route, reports, runs, sources, ws

app.include_router(chat.router)
app.include_router(runs.router)
app.include_router(sources.router)
app.include_router(intent_summary.router)
app.include_router(jobs.router)
app.include_router(artifacts.router)
app.include_router(evidence.router)
app.include_router(ws.router)
app.include_router(experiment_config.router)
app.include_router(experiment_attempts.router)
app.include_router(experiment_projection.router)
app.include_router(report_route.router)
app.include_router(reports.router)
app.include_router(report_collaboration.router)


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

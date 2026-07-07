"""AutoAD Researcher v2 — FastAPI backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

app = FastAPI(title="AutoAD Researcher v2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


from autoad_researcher.server.routes import chat, runs, sources, jobs, artifacts, ws

app.include_router(chat.router)
app.include_router(runs.router)
app.include_router(sources.router)
app.include_router(jobs.router)
app.include_router(artifacts.router)
app.include_router(ws.router)

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
        if full_path.startswith("api/"):
            return {"detail": "Not Found"}
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return {"detail": "Not Found"}

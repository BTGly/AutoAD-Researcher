"""AutoAD Researcher v2 — FastAPI backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

"""FastAPI application: JSON API plus the static single-page frontend."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .chesscom import ChessComError, normalize_username
from .config import settings
from .db import Database
from .engine import EnginePool
from .jobs import DEFAULT_OPTIONS, JobManager, game_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class AnalyzeRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    options: dict[str, Any] | None = None


def create_app(db: Database | None = None, pool: EnginePool | None = None) -> FastAPI:
    app = FastAPI(title="Chess.com Improvement Report", version=__version__)
    app.state.db = db or Database(settings.db_path)
    app.state.pool = pool or EnginePool()
    app.state.jobs = JobManager(app.state.db, app.state.pool)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        app.state.pool.close()
        app.state.db.close()

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {
            "version": __version__,
            "engine": {"available": app.state.pool.available, "path": app.state.pool.path,
                       "workers": app.state.pool.workers},
            "defaults": DEFAULT_OPTIONS,
            "mock": settings.mock_dir is not None,
        }

    @app.get("/api/players")
    def players() -> list[dict[str, Any]]:
        return app.state.db.list_players()

    @app.post("/api/analyze")
    async def analyze(req: AnalyzeRequest) -> dict[str, Any]:
        try:
            job = app.state.jobs.start(req.username, req.options)
        except ChessComError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        j = app.state.jobs.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "unknown job")
        return j.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        return {"cancelled": app.state.jobs.cancel(job_id)}

    @app.get("/api/report/{username}")
    def report(username: str) -> Any:
        try:
            username = normalize_username(username)
        except ChessComError as exc:
            raise HTTPException(400, str(exc)) from exc
        rep = app.state.db.latest_report(username)
        if rep is None:
            raise HTTPException(404, "no report yet for this player")
        return JSONResponse(rep)

    @app.get("/api/games/{username}/{game_id:path}")
    def game(username: str, game_id: str) -> Any:
        try:
            username = normalize_username(username)
        except ChessComError as exc:
            raise HTTPException(400, str(exc)) from exc
        detail = game_detail(app.state.db, username, game_id)
        if detail is None:
            raise HTTPException(404, "unknown game")
        return JSONResponse(detail)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()

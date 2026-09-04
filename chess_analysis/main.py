"""FastAPI application: JSON API plus the static single-page frontend."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, auth, worker
from .chesscom import ChessComError, normalize_username
from .config import settings
from .db import Database
from .engine import EnginePool
from .jobs import ACTIVE_STATES, DEFAULT_OPTIONS, enqueue, game_detail, public_job
from .limits import RateLimiter, client_ip

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class AnalyzeRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    options: dict[str, Any] | None = None


class EmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)


class UsernameRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)


class ProgressRequest(BaseModel):
    progress: dict[str, Any]


def create_app(db: Database | None = None, pool: EnginePool | None = None, inline_worker: bool | None = None) -> FastAPI:
    run_worker = settings.inline_worker if inline_worker is None else inline_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.worker_stop = asyncio.Event()
        app.state.worker_task = None
        if run_worker:
            app.state.worker_task = asyncio.create_task(
                worker.run_forever(app.state.db, app.state.pool, stop_event=app.state.worker_stop))
            log.info("in-process worker started")
        try:
            yield
        finally:
            app.state.worker_stop.set()
            if app.state.worker_task:
                app.state.worker_task.cancel()
                try:
                    await app.state.worker_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            app.state.pool.close()
            app.state.db.close()

    app = FastAPI(title="Chess.com Improvement Report", version=__version__, lifespan=lifespan)
    app.state.db = db or Database(settings.db_path)
    app.state.pool = pool or EnginePool()
    app.state.limiter = RateLimiter()

    # ----- middleware ------------------------------------------------------------------------
    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/api/"):
            ip = client_ip(request)
            if not app.state.limiter.allow(f"ip:{ip}", settings.ip_requests_per_minute, 60):
                return JSONResponse({"detail": "Too many requests. Slow down a little."}, status_code=429)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = CSP
        return response

    # ----- helpers -----------------------------------------------------------------------------
    def get_db() -> Database:
        return app.state.db

    def current_account(request: Request) -> dict[str, Any] | None:
        return auth.account_from_request(request, app.state.db)

    def require_account(request: Request) -> dict[str, Any]:
        account = current_account(request)
        if account is None:
            raise HTTPException(401, "Sign in to use this.")
        return account

    def require_admin(request: Request) -> dict[str, Any]:
        account = require_account(request)
        if not auth.is_admin(account):
            raise HTTPException(403, "Admins only.")
        return account

    def clean_username(username: str) -> str:
        try:
            return normalize_username(username)
        except ChessComError as exc:
            raise HTTPException(400, str(exc)) from exc

    def caps_dict(c: Any) -> dict[str, Any]:
        return {"max_engine_games": c.max_engine_games, "max_depth": c.max_depth, "max_months": c.max_months,
                "jobs_per_day": c.jobs_per_day}

    def usage_for(account: dict[str, Any] | None, ip: str) -> dict[str, Any]:
        caps = auth.caps_for(account)
        since = time.time() - 86400
        used = app.state.db.count_jobs_since(since, requested_by=account["id"]) if account else \
            app.state.db.count_jobs_since(since, client_ip=ip)
        return {"jobs_today": used, "jobs_per_day": caps.jobs_per_day}

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(auth.SESSION_COOKIE, token, max_age=settings.session_days * 86400, httponly=True,
                            samesite="lax", secure=settings.cookie_secure, path="/")

    # ----- status ----------------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        jobs = app.state.db.list_jobs(limit=200)
        return {"ok": True, "engine": app.state.pool.available,
                "queued": sum(1 for j in jobs if j["status"] == "queued"),
                "running": sum(1 for j in jobs if j["status"] in ACTIVE_STATES and j["status"] != "queued")}

    @app.get("/api/status")
    def status(request: Request) -> dict[str, Any]:
        account = current_account(request)
        return {
            "version": __version__,
            "engine": {"available": app.state.pool.available, "workers": app.state.pool.workers},
            "defaults": DEFAULT_OPTIONS,
            "mock": settings.mock_dir is not None,
            "account": auth.public_account(account) if account else None,
            "caps": caps_dict(auth.caps_for(account)),
            "tiers": {"anonymous": caps_dict(settings.anon_caps), "user": caps_dict(settings.user_caps)},
            "usage": usage_for(account, client_ip(request)),
            "contact_email": settings.contact_email,
            "email_enabled": bool(settings.smtp_host) or settings.auth_dev_links,
        }

    # ----- jobs ------------------------------------------------------------------------------
    @app.post("/api/analyze")
    def analyze(req: AnalyzeRequest, request: Request) -> dict[str, Any]:
        account = current_account(request)
        ip = client_ip(request)
        usage = usage_for(account, ip)
        username = clean_username(req.username)
        existing = app.state.db.active_job_for(username)
        if existing:  # joining a running job costs nothing
            return public_job(existing, app.state.db)
        if usage["jobs_today"] >= usage["jobs_per_day"]:
            msg = (f"You have used your {usage['jobs_per_day']} analyses for today."
                   + ("" if account else " Sign in to get a higher daily limit."))
            raise HTTPException(429, msg)
        try:
            job = enqueue(app.state.db, username, req.options, auth.caps_for(account),
                          requested_by=account["id"] if account else None, client_ip=ip,
                          priority=2 if auth.is_admin(account) else (1 if account else 0))
        except ChessComError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return public_job(job, app.state.db)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        row = app.state.db.get_job(job_id)
        if row is None:
            raise HTTPException(404, "unknown job")
        return public_job(row, app.state.db)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request) -> dict[str, Any]:
        row = app.state.db.get_job(job_id)
        if row is None:
            raise HTTPException(404, "unknown job")
        account = current_account(request)
        mine = (account and row.get("requested_by") == account["id"]) or \
               (not row.get("requested_by") and row.get("client_ip") == client_ip(request))
        if not (mine or auth.is_admin(account)):
            raise HTTPException(403, "Only the person who started this analysis can cancel it.")
        return {"cancelled": app.state.db.request_cancel(job_id)}

    # ----- reports & games -------------------------------------------------------------------
    @app.get("/api/report/{username}")
    def report(username: str, request: Request) -> Any:
        username = clean_username(username)
        rep = app.state.db.latest_report(username)
        if rep is None:
            raise HTTPException(404, "no report yet for this player")
        etag = '"' + hashlib.sha1(f"{username}:{rep.get('generated_at')}".encode()).hexdigest()[:16] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(rep, headers={"ETag": etag, "Cache-Control": "public, max-age=120"})

    @app.get("/api/players/{username}/games")
    def player_games(
        username: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        time_class: str | None = None,
        result: str | None = None,
        color: str | None = None,
        analyzed: bool = False,
        q: str | None = None,
    ) -> dict[str, Any]:
        username = clean_username(username)
        games, total = app.state.db.list_game_summaries(
            username, offset=offset, limit=limit, time_class=time_class or None, result=result or None,
            color=color or None, analyzed=analyzed, query=(q or "").strip()[:60] or None,
        )
        return {"games": games, "total": total, "offset": offset, "limit": limit}

    @app.get("/api/players/{username}/history")
    def player_history(username: str) -> list[dict[str, Any]]:
        return app.state.db.history(clean_username(username))

    @app.get("/api/games/{username}/{game_id:path}")
    def game(username: str, game_id: str) -> Any:
        detail = game_detail(app.state.db, clean_username(username), game_id[:200])
        if detail is None:
            raise HTTPException(404, "unknown game")
        return JSONResponse(detail, headers={"Cache-Control": "public, max-age=300"})

    # ----- accounts --------------------------------------------------------------------------
    @app.post("/api/auth/request-link")
    def request_link(req: EmailRequest, request: Request) -> dict[str, Any]:
        ip = client_ip(request)
        if not app.state.limiter.allow(f"login:{ip}", settings.login_links_per_hour * 2, 3600):
            raise HTTPException(429, "Too many sign-in requests from your address. Try again later.")
        try:
            return auth.request_login(app.state.db, req.email)
        except auth.AuthError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - SMTP trouble must not leak details
            log.exception("could not send sign-in email")
            raise HTTPException(502, "The sign-in email could not be sent. Try again in a minute.") from exc

    @app.post("/api/auth/verify")
    def verify(req: TokenRequest, response: Response) -> dict[str, Any]:
        try:
            account, token = auth.verify_login(app.state.db, req.token)
        except auth.AuthError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        set_session_cookie(response, token)
        return {"account": auth.public_account(account)}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        auth.logout(request, app.state.db)
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request, account: dict[str, Any] = Depends(require_account)) -> dict[str, Any]:
        return {"account": auth.public_account(account), "players": app.state.db.saved_players(account["id"]),
                "caps": caps_dict(auth.caps_for(account)), "usage": usage_for(account, client_ip(request))}

    @app.post("/api/me/players")
    def save_player(req: UsernameRequest, account: dict[str, Any] = Depends(require_account)) -> dict[str, Any]:
        app.state.db.save_player_for(account["id"], clean_username(req.username))
        return {"players": app.state.db.saved_players(account["id"])}

    @app.delete("/api/me/players/{username}")
    def unsave_player(username: str, account: dict[str, Any] = Depends(require_account)) -> dict[str, Any]:
        app.state.db.remove_player_for(account["id"], clean_username(username))
        return {"players": app.state.db.saved_players(account["id"])}

    @app.get("/api/me/puzzles/{username}")
    def puzzle_progress(username: str, account: dict[str, Any] = Depends(require_account)) -> dict[str, Any]:
        return {"progress": app.state.db.get_puzzle_progress(account["id"], clean_username(username))}

    @app.put("/api/me/puzzles/{username}")
    def set_puzzle_progress(username: str, req: ProgressRequest,
                            account: dict[str, Any] = Depends(require_account)) -> dict[str, Any]:
        if len(req.progress) > 5000:
            raise HTTPException(413, "too much progress data")
        app.state.db.set_puzzle_progress(account["id"], clean_username(username), req.progress)
        return {"ok": True}

    # ----- admin -----------------------------------------------------------------------------
    @app.get("/api/admin/jobs")
    def admin_jobs(_: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
        return [public_job(j, app.state.db) | {"requested_by": j.get("requested_by"), "client_ip": j.get("client_ip")}
                for j in app.state.db.list_jobs(limit=100)]

    @app.delete("/api/admin/players/{username}")
    def admin_delete_player(username: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        app.state.db.delete_player_data(clean_username(username))
        return {"deleted": username}

    # ----- static ----------------------------------------------------------------------------
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()

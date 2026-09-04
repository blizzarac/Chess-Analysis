"""Analysis jobs: a persistent queue in SQLite, and the pipeline a worker runs for each job.

The web process only enqueues and reads jobs. A worker (separate process, or an in-process
task in development) claims queued jobs and runs: download -> parse -> engine -> MultiPV
verification -> report. Progress is written back to the jobs table so any process can show it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .analysis.game_analysis import annotate
from .analysis.report import build_report, puzzle_candidates, report_summary
from .chesscom import ChessComClient, ChessComError, normalize_username
from .config import Caps, settings
from .db import Database
from .engine import EnginePool, EngineUnavailable
from .pgn_parse import ParsedGame, parse_game

log = logging.getLogger(__name__)

DEFAULT_OPTIONS: dict[str, Any] = {
    "time_classes": ["bullet", "blitz", "rapid", "daily"],
    "max_engine_games": 100,
    "depth": 14,
    "max_months": None,       # None = every archived month
    "refresh": False,         # re-download months already cached
}
ACTIVE_STATES = ("queued", "fetching", "analyzing", "reporting")
FINAL_STATES = ("done", "error", "cancelled")


class JobCancelled(Exception):
    pass


def options_key(options: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(options, sort_keys=True).encode()).hexdigest()[:12]


def clamp_options(raw: dict[str, Any] | None, caps: Caps) -> dict[str, Any]:
    """Merge user options with defaults and keep them inside the visitor's caps."""
    opts = dict(DEFAULT_OPTIONS)
    opts.update({k: v for k, v in (raw or {}).items() if k in DEFAULT_OPTIONS and v is not None})
    opts["depth"] = int(max(6, min(caps.max_depth, int(opts["depth"]))))
    opts["max_engine_games"] = int(max(0, min(caps.max_engine_games, int(opts["max_engine_games"]))))
    tcs = [t for t in opts["time_classes"] if t in DEFAULT_OPTIONS["time_classes"]] or list(DEFAULT_OPTIONS["time_classes"])
    opts["time_classes"] = sorted(tcs)
    months = opts.get("max_months")
    months = int(months) if months else None
    if caps.max_months is not None:
        months = min(months, caps.max_months) if months else caps.max_months
    opts["max_months"] = months
    opts["refresh"] = bool(opts.get("refresh"))
    return opts


def public_job(row: dict[str, Any], db: Database | None = None) -> dict[str, Any]:
    """The subset of a job row the frontend may see."""
    out = {k: row.get(k) for k in ("id", "username", "options", "status", "stage_detail", "progress", "error",
                                   "created_at", "started_at", "finished_at")}
    if db is not None and row.get("status") == "queued":
        out["queue_position"] = db.queue_position(row["id"])
    return out


def enqueue(db: Database, username: str, options: dict[str, Any] | None, caps: Caps,
            requested_by: str | None = None, client_ip: str | None = None, priority: int = 0) -> dict[str, Any]:
    """Create a job, or return the job already running for this player."""
    username = normalize_username(username)
    existing = db.active_job_for(username)
    if existing:
        return existing
    opts = clamp_options(options, caps)
    return db.enqueue_job(uuid.uuid4().hex[:10], username, opts, requested_by, client_ip, priority)


class JobHandle:
    """Local view of a job row with throttled writes back to the database."""

    def __init__(self, db: Database, row: dict[str, Any]):
        self.db = db
        self.id: str = row["id"]
        self.username: str = row["username"]
        self.options: dict[str, Any] = row["options"]
        self.status: str = row["status"]
        self.stage_detail: str = row.get("stage_detail") or ""
        self.progress: dict[str, Any] = dict(row.get("progress") or {})
        self._last_flush = 0.0
        self._last_cancel_check = 0.0
        self._cancelled = False

    def set(self, status: str | None = None, stage_detail: str | None = None, **progress: Any) -> None:
        force = False
        if status is not None and status != self.status:
            self.status, force = status, True
        if stage_detail is not None:
            self.stage_detail = stage_detail
        self.progress.update(progress)
        self.flush(force=force)

    def flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_flush < 0.5:
            return
        self._last_flush = now
        self.db.update_job(self.id, status=self.status, stage_detail=self.stage_detail, progress=self.progress)

    def check_cancel(self) -> None:
        """Raise JobCancelled if the user asked to stop (checked at most twice a second)."""
        now = time.time()
        if self._cancelled or now - self._last_cancel_check >= 0.5:
            self._last_cancel_check = now
            if self._cancelled or self.db.cancel_requested(self.id):
                self._cancelled = True
                raise JobCancelled()

    def finish(self, status: str, error: str | None = None) -> None:
        self.status = status
        self.db.update_job(self.id, status=status, error=error, stage_detail=self.stage_detail,
                           progress=self.progress, finished_at=time.time())


class JobRunner:
    """Runs one job end to end. Used by the worker loop and by the tests."""

    def __init__(self, db: Database, pool: EnginePool):
        self.db = db
        self.pool = pool

    async def run(self, row: dict[str, Any]) -> dict[str, Any]:
        job = JobHandle(self.db, row)
        try:
            await self._fetch(job)
            games = self._load_games(job)
            await self._analyze(job, games)
            await self._verify_puzzles(job, games)
            self._report(job, games)
            job.finish("done")
        except JobCancelled:
            job.finish("cancelled")
        except (ChessComError, EngineUnavailable) as exc:
            job.finish("error", str(exc))
        except asyncio.CancelledError:
            job.finish("cancelled", "worker stopped")
            raise
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            log.exception("job %s failed", job.id)
            job.finish("error", f"{type(exc).__name__}: {exc}")
        return self.db.get_job(job.id) or row

    # ------------------------------------------------------------------------------------------
    async def _fetch(self, job: JobHandle) -> None:
        job.set(status="fetching", stage_detail="Looking up player")
        async with ChessComClient() as client:
            profile = await client.profile(job.username)
            stats = await client.stats(job.username)
            self.db.save_player(job.username, profile, stats)
            archives = await client.archives(job.username)
            months = []
            for url in archives:
                y, m = url.rstrip("/").split("/")[-2:]
                months.append((int(y), int(m)))
            months.sort(reverse=True)
            if job.options.get("max_months"):
                months = months[: int(job.options["max_months"])]
            cached = set() if job.options.get("refresh") else self.db.complete_months(job.username)
            now = datetime.now(timezone.utc)
            todo = [(y, m) for (y, m) in months if (y, m) not in cached]
            job.set(months_total=len(months), months_cached=len(months) - len(todo), months_done=0, games_downloaded=0)
            for i, (y, m) in enumerate(todo):
                job.check_cancel()
                job.set(stage_detail=f"Downloading {y}-{m:02d} ({i + 1}/{len(todo)})")
                games = await client.month_games(job.username, y, m)
                n = self.db.upsert_games(job.username, games)
                is_current = (y, m) == (now.year, now.month)
                self.db.mark_month(job.username, y, m, complete=not is_current)
                job.set(months_done=i + 1, games_downloaded=job.progress["games_downloaded"] + n)

    def _load_games(self, job: JobHandle) -> list[ParsedGame]:
        job.set(stage_detail="Parsing games")
        raw_games = self.db.games_for(job.username, with_analysis=True)
        parsed: list[ParsedGame] = []
        for raw in raw_games:
            try:
                g = parse_game(raw, job.username)
            except Exception as exc:  # noqa: BLE001 - one bad PGN must not sink the run
                log.warning("could not parse game %s: %s", raw.get("url"), exc)
                continue
            if g is None or not g.moves:
                continue
            g.headers["_raw_analysis"] = raw.get("_analysis")  # type: ignore[assignment]
            g.headers["_raw_depth"] = raw.get("_analysis_depth")  # type: ignore[assignment]
            parsed.append(g)
        job.set(games_total=len(parsed))
        return parsed

    async def _analyze(self, job: JobHandle, games: list[ParsedGame]) -> None:
        job.set(status="analyzing")
        depth = int(job.options["depth"])
        wanted_tc = set(job.options["time_classes"])
        candidates = [
            g for g in sorted(games, key=lambda g: -g.end_time)
            if g.time_class in wanted_tc and g.rules == "chess" and len(g.moves) >= 6
        ]
        selected = candidates[: int(job.options["max_engine_games"])]
        todo = [g for g in selected if not (g.headers.get("_raw_analysis") and (g.headers.get("_raw_depth") or 0) >= depth)]
        job.set(engine_total=len(selected), engine_done=len(selected) - len(todo), eta_seconds=None)
        if not todo:
            return
        if not self.pool.available:
            job.set(stage_detail="Stockfish not found: skipping engine analysis", engine_skipped=True)
            return
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(self.pool.workers)
        started = time.time()
        done_counter = 0

        async def run_one(g: ParsedGame) -> None:
            nonlocal done_counter
            async with sem:
                job.check_cancel()
                evals = await loop.run_in_executor(
                    self.pool.executor(), self.pool.analyze_positions,
                    [m.uci for m in g.moves], depth, g.moves[0].fen_before, g.rules == "chess960",
                )
                self.db.save_analysis(job.username, g.id, {"evals": evals}, depth)
                g.headers["_raw_analysis"] = {"evals": evals}  # type: ignore[assignment]
                g.headers["_raw_depth"] = depth  # type: ignore[assignment]
                done_counter += 1
                elapsed = time.time() - started
                remaining = len(todo) - done_counter
                job.set(
                    stage_detail=f"Engine analysis {job.progress['engine_done'] + 1}/{job.progress['engine_total']} (depth {depth})",
                    engine_done=job.progress["engine_done"] + 1,
                    eta_seconds=int(elapsed / done_counter * remaining) if done_counter else None,
                )

        job.set(stage_detail=f"Engine analysis 0/{len(selected)} (depth {depth})")
        await asyncio.gather(*(run_one(g) for g in todo))

    async def _verify_puzzles(self, job: JobHandle, games: list[ParsedGame]) -> None:
        """Second engine pass: MultiPV on the positions that will become puzzles."""
        if not self.pool.available:
            return
        depth = int(job.options["depth"])
        todo: list[tuple[ParsedGame, int, str]] = []
        for g in games:
            raw = g.headers.get("_raw_analysis")
            if not isinstance(raw, dict) or not raw.get("evals"):
                continue
            have = raw.get("multipv") or {}
            try:
                ann = annotate(g, raw["evals"])
            except Exception:  # noqa: BLE001
                continue
            for cand in puzzle_candidates(g, ann):
                if str(cand["ply"]) not in have:
                    todo.append((g, cand["ply"], cand["fen"]))
        if not todo:
            return
        job.set(verify_total=len(todo), verify_done=0, stage_detail=f"Verifying puzzle positions 0/{len(todo)}")
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(self.pool.workers)

        async def run_one(g: ParsedGame, ply: int, fen: str) -> None:
            async with sem:
                job.check_cancel()
                alts = await loop.run_in_executor(self.pool.executor(), self.pool.analyze_multipv, fen, depth, 3,
                                                  g.rules == "chess960")
                raw = g.headers["_raw_analysis"]  # type: ignore[index]
                raw.setdefault("multipv", {})[str(ply)] = alts  # type: ignore[union-attr]
                self.db.save_analysis(job.username, g.id, raw, int(g.headers.get("_raw_depth") or depth))  # type: ignore[arg-type]
                job.set(verify_done=job.progress["verify_done"] + 1,
                        stage_detail=f"Verifying puzzle positions {job.progress['verify_done'] + 1}/{len(todo)}")

        await asyncio.gather(*(run_one(g, ply, fen) for g, ply, fen in todo))

    def _report(self, job: JobHandle, games: list[ParsedGame]) -> None:
        job.set(status="reporting", stage_detail="Building report")
        job.check_cancel()
        annotations: dict[str, dict[str, Any]] = {}
        for g in games:
            raw = g.headers.get("_raw_analysis")
            evals = raw.get("evals") if isinstance(raw, dict) else None
            multipv = raw.get("multipv") if isinstance(raw, dict) else None
            try:
                annotations[g.id] = annotate(g, evals, multipv)
            except Exception as exc:  # noqa: BLE001
                log.warning("annotation failed for %s: %s", g.id, exc)
        player = self.db.get_player(job.username) or {}
        previous = self.db.history(job.username, limit=1)
        report = build_report(job.username, player.get("profile", {}), player.get("stats", {}), games, annotations,
                              job.options, previous=previous[0] if previous else None)
        summaries = {row["id"]: row for row in report.pop("games")}
        self.db.save_game_summaries(job.username, summaries)
        self.db.save_report(job.username, options_key(job.options), report)
        self.db.add_history(job.username, report_summary(report))
        job.set(report_key=options_key(job.options))


def game_detail(db: Database, username: str, game_id: str) -> dict[str, Any] | None:
    raw = db.get_game(username, game_id)
    if raw is None:
        return None
    g = parse_game(raw, username)
    if g is None:
        return None
    analysis = raw.get("_analysis") or {}
    ann = annotate(g, analysis.get("evals"), analysis.get("multipv"))
    info = g.to_dict()
    info.pop("moves", None)
    info.pop("headers", None)
    return {"game": info, "analysis": ann, "pgn": raw.get("pgn")}

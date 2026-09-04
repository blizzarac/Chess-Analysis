"""Background pipeline: download games, run the engine, build the report. Tracks progress."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .analysis.game_analysis import annotate
from .analysis.report import build_report, puzzle_candidates, report_summary
from .chesscom import ChessComClient, ChessComError, normalize_username
from .config import settings
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


def options_key(options: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(options, sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class Job:
    id: str
    username: str
    options: dict[str, Any]
    status: str = "queued"          # queued | fetching | analyzing | reporting | done | error | cancelled
    stage_detail: str = ""
    progress: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "options": self.options,
            "status": self.status,
            "stage_detail": self.stage_detail,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self, db: Database, pool: EnginePool):
        self.db = db
        self.pool = pool
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def active_job_for(self, username: str) -> Job | None:
        for job in self.jobs.values():
            if job.username == username and job.status not in ("done", "error", "cancelled"):
                return job
        return None

    def start(self, username: str, options: dict[str, Any] | None) -> Job:
        username = normalize_username(username)
        opts = dict(DEFAULT_OPTIONS)
        opts.update({k: v for k, v in (options or {}).items() if k in DEFAULT_OPTIONS and v is not None})
        opts["depth"] = int(max(6, min(24, opts["depth"])))
        opts["max_engine_games"] = int(max(0, min(2000, opts["max_engine_games"])))
        existing = self.active_job_for(username)
        if existing:
            return existing
        job = Job(id=uuid.uuid4().hex[:10], username=username, options=opts)
        self.jobs[job.id] = job
        self._tasks[job.id] = asyncio.create_task(self._run(job))
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status in ("done", "error", "cancelled"):
            return False
        job.cancel_requested = True
        return True

    # ------------------------------------------------------------------------------------------
    async def _run(self, job: Job) -> None:
        try:
            await self._fetch(job)
            games = self._load_games(job)
            await self._analyze(job, games)
            await self._verify_puzzles(job, games)
            self._report(job, games)
            job.status = "done"
        except ChessComError as exc:
            job.status = "error"
            job.error = str(exc)
        except EngineUnavailable as exc:
            job.status = "error"
            job.error = str(exc)
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            log.exception("job %s failed", job.id)
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = time.time()

    async def _fetch(self, job: Job) -> None:
        job.status = "fetching"
        job.stage_detail = "Looking up player"
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
            job.progress = {"months_total": len(months), "months_cached": len(months) - len(todo), "months_done": 0,
                            "games_downloaded": 0}
            for i, (y, m) in enumerate(todo):
                if job.cancel_requested:
                    job.status = "cancelled"
                    raise asyncio.CancelledError
                job.stage_detail = f"Downloading {y}-{m:02d} ({i + 1}/{len(todo)})"
                games = await client.month_games(job.username, y, m)
                n = self.db.upsert_games(job.username, games)
                is_current = (y, m) == (now.year, now.month)
                self.db.mark_month(job.username, y, m, complete=not is_current)
                job.progress["months_done"] = i + 1
                job.progress["games_downloaded"] += n
                await asyncio.sleep(0)  # let the API breathe between months

    def _load_games(self, job: Job) -> list[ParsedGame]:
        job.stage_detail = "Parsing games"
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
        job.progress["games_total"] = len(parsed)
        return parsed

    async def _analyze(self, job: Job, games: list[ParsedGame]) -> None:
        job.status = "analyzing"
        depth = int(job.options["depth"])
        wanted_tc = set(job.options["time_classes"])
        candidates = [
            g for g in sorted(games, key=lambda g: -g.end_time)
            if g.time_class in wanted_tc and g.rules == "chess" and len(g.moves) >= 6
        ]
        limit = int(job.options["max_engine_games"])
        selected = candidates[:limit]
        todo = [g for g in selected if not (g.headers.get("_raw_analysis") and (g.headers.get("_raw_depth") or 0) >= depth)]
        job.progress.update({"engine_total": len(selected), "engine_done": len(selected) - len(todo),
                             "engine_started": time.time(), "eta_seconds": None})
        if not todo:
            return
        if not self.pool.available:
            job.stage_detail = "Stockfish not found: skipping engine analysis"
            job.progress["engine_skipped"] = True
            return
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(self.pool.workers)
        started = time.time()
        done_counter = 0

        async def run_one(g: ParsedGame) -> None:
            nonlocal done_counter
            async with sem:
                if job.cancel_requested:
                    return
                evals = await loop.run_in_executor(
                    self.pool.executor(),
                    self.pool.analyze_positions,
                    [m.uci for m in g.moves], depth, g.moves[0].fen_before, g.rules == "chess960",
                )
                self.db.save_analysis(job.username, g.id, {"evals": evals}, depth)
                g.headers["_raw_analysis"] = {"evals": evals}  # type: ignore[assignment]
                g.headers["_raw_depth"] = depth  # type: ignore[assignment]
                done_counter += 1
                job.progress["engine_done"] += 1
                elapsed = time.time() - started
                remaining = len(todo) - done_counter
                job.progress["eta_seconds"] = int(elapsed / done_counter * remaining) if done_counter else None
                job.stage_detail = f"Engine analysis {job.progress['engine_done']}/{job.progress['engine_total']} (depth {depth})"

        job.stage_detail = f"Engine analysis 0/{len(selected)} (depth {depth})"
        await asyncio.gather(*(run_one(g) for g in todo))
        if job.cancel_requested:
            raise asyncio.CancelledError

    async def _verify_puzzles(self, job: Job, games: list[ParsedGame]) -> None:
        """Second engine pass: MultiPV on the positions that will become puzzles, so that
        alternative solutions are accepted and false positives from single-PV analysis are dropped."""
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
        job.status = "analyzing"
        job.progress.update({"verify_total": len(todo), "verify_done": 0})
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(self.pool.workers)

        async def run_one(g: ParsedGame, ply: int, fen: str) -> None:
            async with sem:
                if job.cancel_requested:
                    return
                alts = await loop.run_in_executor(self.pool.executor(), self.pool.analyze_multipv, fen, depth, 3,
                                                  g.rules == "chess960")
                raw = g.headers["_raw_analysis"]  # type: ignore[index]
                raw.setdefault("multipv", {})[str(ply)] = alts  # type: ignore[union-attr]
                self.db.save_analysis(job.username, g.id, raw, int(g.headers.get("_raw_depth") or depth))  # type: ignore[arg-type]
                job.progress["verify_done"] += 1
                job.stage_detail = f"Verifying puzzle positions {job.progress['verify_done']}/{len(todo)}"

        job.stage_detail = f"Verifying puzzle positions 0/{len(todo)}"
        await asyncio.gather(*(run_one(g, ply, fen) for g, ply, fen in todo))
        if job.cancel_requested:
            raise asyncio.CancelledError

    def _report(self, job: Job, games: list[ParsedGame]) -> None:
        job.status = "reporting"
        job.stage_detail = "Building report"
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
        job.progress["report_key"] = options_key(job.options)


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

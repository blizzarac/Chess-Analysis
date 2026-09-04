"""SQLite storage for downloaded games, engine analysis and generated reports."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    username     TEXT PRIMARY KEY,
    profile_json TEXT,
    stats_json   TEXT,
    fetched_at   REAL
);
CREATE TABLE IF NOT EXISTS months (
    username   TEXT NOT NULL,
    year       INTEGER NOT NULL,
    month      INTEGER NOT NULL,
    complete   INTEGER NOT NULL DEFAULT 0,
    fetched_at REAL,
    PRIMARY KEY (username, year, month)
);
CREATE TABLE IF NOT EXISTS games (
    id            TEXT NOT NULL,
    username      TEXT NOT NULL,
    end_time      INTEGER,
    time_class    TEXT,
    rules         TEXT,
    raw_json      TEXT NOT NULL,
    analysis_json TEXT,
    analysis_depth INTEGER,
    PRIMARY KEY (id, username)
);
CREATE INDEX IF NOT EXISTS games_user_time ON games (username, end_time DESC);
CREATE TABLE IF NOT EXISTS report_history (
    username    TEXT NOT NULL,
    created_at  REAL NOT NULL,
    summary_json TEXT NOT NULL,
    PRIMARY KEY (username, created_at)
);
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    options_json  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    stage_detail  TEXT NOT NULL DEFAULT '',
    progress_json TEXT NOT NULL DEFAULT '{}',
    error         TEXT,
    requested_by  TEXT,
    client_ip     TEXT,
    priority      INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    worker_id     TEXT,
    heartbeat     REAL,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, priority DESC, created_at);
CREATE TABLE IF NOT EXISTS accounts (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_login_at REAL
);
CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at    REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS account_players (
    account_id TEXT NOT NULL,
    username   TEXT NOT NULL,
    added_at   REAL NOT NULL,
    PRIMARY KEY (account_id, username)
);
CREATE TABLE IF NOT EXISTS puzzle_progress (
    account_id TEXT NOT NULL,
    username   TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account_id, username)
);
CREATE TABLE IF NOT EXISTS reports (
    username    TEXT NOT NULL,
    options_key TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at  REAL,
    PRIMARY KEY (username, options_key)
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(games)")}
            if "summary_json" not in cols:
                self._conn.execute("ALTER TABLE games ADD COLUMN summary_json TEXT")
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- players ---------------------------------------------------------------------------
    def save_player(self, username: str, profile: dict[str, Any], stats: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO players (username, profile_json, stats_json, fetched_at) VALUES (?,?,?,?)"
                " ON CONFLICT(username) DO UPDATE SET profile_json=excluded.profile_json,"
                " stats_json=excluded.stats_json, fetched_at=excluded.fetched_at",
                (username, json.dumps(profile), json.dumps(stats), time.time()),
            )
            self._conn.commit()

    def get_player(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM players WHERE username=?", (username,)).fetchone()
        if row is None:
            return None
        return {
            "username": row["username"],
            "profile": json.loads(row["profile_json"] or "{}"),
            "stats": json.loads(row["stats_json"] or "{}"),
            "fetched_at": row["fetched_at"],
        }

    def list_players(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.username, p.profile_json, p.fetched_at, COUNT(g.id) AS n_games,"
                " SUM(CASE WHEN g.analysis_json IS NOT NULL THEN 1 ELSE 0 END) AS n_analyzed"
                " FROM players p LEFT JOIN games g ON g.username = p.username"
                " GROUP BY p.username ORDER BY p.fetched_at DESC"
            ).fetchall()
        return [
            {
                "username": r["username"],
                "profile": json.loads(r["profile_json"] or "{}"),
                "fetched_at": r["fetched_at"],
                "games": r["n_games"],
                "analyzed": r["n_analyzed"],
            }
            for r in rows
        ]

    # ----- months ----------------------------------------------------------------------------
    def complete_months(self, username: str) -> set[tuple[int, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT year, month FROM months WHERE username=? AND complete=1", (username,)
            ).fetchall()
        return {(r["year"], r["month"]) for r in rows}

    def mark_month(self, username: str, year: int, month: int, complete: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO months (username, year, month, complete, fetched_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(username, year, month) DO UPDATE SET complete=excluded.complete,"
                " fetched_at=excluded.fetched_at",
                (username, year, month, int(complete), time.time()),
            )
            self._conn.commit()

    # ----- games -----------------------------------------------------------------------------
    def upsert_games(self, username: str, games: Iterable[dict[str, Any]]) -> int:
        n = 0
        with self._lock:
            for g in games:
                gid = g.get("uuid") or g.get("url")
                if not gid:
                    continue
                self._conn.execute(
                    "INSERT INTO games (id, username, end_time, time_class, rules, raw_json)"
                    " VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(id, username) DO UPDATE SET raw_json=excluded.raw_json,"
                    " end_time=excluded.end_time, time_class=excluded.time_class, rules=excluded.rules",
                    (gid, username, g.get("end_time"), g.get("time_class"), g.get("rules"), json.dumps(g)),
                )
                n += 1
            self._conn.commit()
        return n

    def games_for(self, username: str, with_analysis: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, raw_json, analysis_json, analysis_depth FROM games WHERE username=?"
                " ORDER BY end_time DESC",
                (username,),
            ).fetchall()
        out = []
        for r in rows:
            g = json.loads(r["raw_json"])
            g["_id"] = r["id"]
            if with_analysis and r["analysis_json"]:
                g["_analysis"] = json.loads(r["analysis_json"])
                g["_analysis_depth"] = r["analysis_depth"]
            out.append(g)
        return out

    def get_game(self, username: str, game_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT id, raw_json, analysis_json, analysis_depth FROM games WHERE username=? AND id=?",
                (username, game_id),
            ).fetchone()
        if r is None:
            return None
        g = json.loads(r["raw_json"])
        g["_id"] = r["id"]
        if r["analysis_json"]:
            g["_analysis"] = json.loads(r["analysis_json"])
            g["_analysis_depth"] = r["analysis_depth"]
        return g

    def save_analysis(self, username: str, game_id: str, analysis: dict[str, Any], depth: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE games SET analysis_json=?, analysis_depth=? WHERE username=? AND id=?",
                (json.dumps(analysis), depth, username, game_id),
            )
            self._conn.commit()

    def save_game_summaries(self, username: str, summaries: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            self._conn.executemany(
                "UPDATE games SET summary_json=? WHERE username=? AND id=?",
                [(json.dumps(v), username, k) for k, v in summaries.items()],
            )
            self._conn.commit()

    def list_game_summaries(
        self, username: str, offset: int = 0, limit: int = 50, time_class: str | None = None,
        result: str | None = None, color: str | None = None, analyzed: bool | None = None,
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Filtered, paginated game summaries (as stored by the report builder)."""
        where = ["username=?", "summary_json IS NOT NULL"]
        params: list[Any] = [username]
        if time_class:
            where.append("time_class=?")
            params.append(time_class)
        if result:
            where.append("json_extract(summary_json, '$.result')=?")
            params.append(result)
        if color:
            where.append("json_extract(summary_json, '$.color')=?")
            params.append(color)
        if analyzed:
            where.append("json_extract(summary_json, '$.analyzed')=1")
        if query:
            where.append("(lower(json_extract(summary_json, '$.opponent')) LIKE ? OR lower(json_extract(summary_json, '$.opening')) LIKE ?)")
            like = f"%{query.lower()}%"
            params.extend([like, like])
        sql_where = " AND ".join(where)
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM games WHERE {sql_where}", params).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT summary_json FROM games WHERE {sql_where} ORDER BY end_time DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [json.loads(r["summary_json"]) for r in rows], int(total)

    def add_history(self, username: str, summary: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO report_history (username, created_at, summary_json) VALUES (?,?,?)",
                (username, time.time(), json.dumps(summary)),
            )
            self._conn.commit()

    def history(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT created_at, summary_json FROM report_history WHERE username=? ORDER BY created_at DESC LIMIT ?",
                (username, limit),
            ).fetchall()
        return [{"created_at": r["created_at"], **json.loads(r["summary_json"])} for r in rows]

    def count_games(self, username: str) -> tuple[int, int]:
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS n, SUM(CASE WHEN analysis_json IS NOT NULL THEN 1 ELSE 0 END) AS a"
                " FROM games WHERE username=?",
                (username,),
            ).fetchone()
        return int(r["n"] or 0), int(r["a"] or 0)

    # ----- jobs (persistent queue) -----------------------------------------------------------
    def _job_row(self, r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        d["options"] = json.loads(d.pop("options_json"))
        d["progress"] = json.loads(d.pop("progress_json") or "{}")
        d["cancel_requested"] = bool(d["cancel_requested"])
        return d

    def enqueue_job(self, job_id: str, username: str, options: dict[str, Any], requested_by: str | None,
                    client_ip: str | None, priority: int = 0) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, username, options_json, requested_by, client_ip, priority, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (job_id, username, json.dumps(options), requested_by, client_ip, priority, time.time()),
            )
            self._conn.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job_row(r)

    def active_job_for(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM jobs WHERE username=? AND status IN ('queued','fetching','analyzing','reporting')"
                " ORDER BY created_at DESC LIMIT 1", (username,)).fetchone()
        return self._job_row(r)

    def claim_job(self, worker_id: str) -> dict[str, Any] | None:
        """Atomically take the next queued job."""
        with self._lock:
            r = self._conn.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY priority DESC, created_at LIMIT 1").fetchone()
            if r is None:
                return None
            now = time.time()
            cur = self._conn.execute(
                "UPDATE jobs SET status='fetching', worker_id=?, started_at=?, heartbeat=? WHERE id=? AND status='queued'",
                (worker_id, now, now, r["id"]))
            self._conn.commit()
            if cur.rowcount != 1:
                return None
        return self.get_job(r["id"])

    def update_job(self, job_id: str, **fields: Any) -> None:
        if "progress" in fields:
            fields["progress_json"] = json.dumps(fields.pop("progress"))
        fields["heartbeat"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", [*fields.values(), job_id])
            self._conn.commit()

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET cancel_requested=1 WHERE id=? AND status IN ('queued','fetching','analyzing','reporting')",
                (job_id,))
            self._conn.commit()
        return cur.rowcount == 1

    def cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            r = self._conn.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        return bool(r and r["cancel_requested"])

    def requeue_stale_jobs(self, older_than: float) -> int:
        """Jobs whose worker stopped reporting (crash, redeploy) go back to the queue."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status='queued', worker_id=NULL, stage_detail='Restarting after interruption'"
                " WHERE status IN ('fetching','analyzing','reporting') AND (heartbeat IS NULL OR heartbeat < ?)",
                (time.time() - older_than,))
            self._conn.commit()
        return cur.rowcount

    def count_jobs_since(self, since: float, requested_by: str | None = None, client_ip: str | None = None) -> int:
        clauses, params = ["created_at >= ?"], [since]
        if requested_by is not None:
            clauses.append("requested_by=?")
            params.append(requested_by)
        elif client_ip is not None:
            clauses.append("client_ip=? AND requested_by IS NULL")
            params.append(client_ip)
        with self._lock:
            r = self._conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {' AND '.join(clauses)}", params).fetchone()
        return int(r[0])

    def queue_position(self, job_id: str) -> int | None:
        with self._lock:
            job = self._conn.execute("SELECT status, priority, created_at FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["status"] != "queued":
                return None
            r = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='queued' AND (priority > ? OR (priority = ? AND created_at < ?))",
                (job["priority"], job["priority"], job["created_at"])).fetchone()
        return int(r[0]) + 1

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._job_row(r) for r in rows]  # type: ignore[misc]

    # ----- accounts ---------------------------------------------------------------------------
    def create_login_token(self, token_hash: str, email: str, expires_at: float) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO login_tokens (token_hash, email, created_at, expires_at) VALUES (?,?,?,?)",
                               (token_hash, email, time.time(), expires_at))
            self._conn.commit()

    def consume_login_token(self, token_hash: str) -> str | None:
        """Mark the token used and return its email, or None if unknown, used or expired."""
        with self._lock:
            r = self._conn.execute("SELECT email, expires_at, used_at FROM login_tokens WHERE token_hash=?",
                                   (token_hash,)).fetchone()
            if r is None or r["used_at"] is not None or r["expires_at"] < time.time():
                return None
            self._conn.execute("UPDATE login_tokens SET used_at=? WHERE token_hash=?", (time.time(), token_hash))
            self._conn.commit()
        return r["email"]

    def count_login_tokens_since(self, email: str, since: float) -> int:
        with self._lock:
            r = self._conn.execute("SELECT COUNT(*) FROM login_tokens WHERE email=? AND created_at >= ?",
                                   (email, since)).fetchone()
        return int(r[0])

    def get_or_create_account(self, account_id: str, email: str) -> dict[str, Any]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if r is None:
                self._conn.execute("INSERT INTO accounts (id, email, created_at, last_login_at) VALUES (?,?,?,?)",
                                   (account_id, email, time.time(), time.time()))
            else:
                self._conn.execute("UPDATE accounts SET last_login_at=? WHERE id=?", (time.time(), r["id"]))
            self._conn.commit()
            r = self._conn.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
        return dict(r)

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return dict(r) if r else None

    def create_session(self, token_hash: str, account_id: str, expires_at: float) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?,?,?,?)",
                               (token_hash, account_id, time.time(), expires_at))
            self._conn.commit()

    def account_for_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT a.* FROM sessions s JOIN accounts a ON a.id = s.account_id"
                " WHERE s.token_hash=? AND s.expires_at > ?", (token_hash, time.time())).fetchone()
        return dict(r) if r else None

    def delete_session(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            self._conn.commit()

    def saved_players(self, account_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ap.username, ap.added_at, p.fetched_at,"
                " (SELECT COUNT(*) FROM games g WHERE g.username = ap.username) AS n_games,"
                " (SELECT COUNT(*) FROM games g WHERE g.username = ap.username AND g.analysis_json IS NOT NULL) AS n_analyzed"
                " FROM account_players ap LEFT JOIN players p ON p.username = ap.username"
                " WHERE ap.account_id=? ORDER BY ap.added_at DESC", (account_id,)).fetchall()
        return [{"username": r["username"], "added_at": r["added_at"], "fetched_at": r["fetched_at"],
                 "games": r["n_games"], "analyzed": r["n_analyzed"]} for r in rows]

    def save_player_for(self, account_id: str, username: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR IGNORE INTO account_players (account_id, username, added_at) VALUES (?,?,?)",
                               (account_id, username, time.time()))
            self._conn.commit()

    def remove_player_for(self, account_id: str, username: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM account_players WHERE account_id=? AND username=?", (account_id, username))
            self._conn.commit()

    def get_puzzle_progress(self, account_id: str, username: str) -> dict[str, Any]:
        with self._lock:
            r = self._conn.execute("SELECT progress_json FROM puzzle_progress WHERE account_id=? AND username=?",
                                   (account_id, username)).fetchone()
        return json.loads(r["progress_json"]) if r else {}

    def set_puzzle_progress(self, account_id: str, username: str, progress: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO puzzle_progress (account_id, username, progress_json, updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(account_id, username) DO UPDATE SET progress_json=excluded.progress_json,"
                " updated_at=excluded.updated_at", (account_id, username, json.dumps(progress), time.time()))
            self._conn.commit()

    def delete_player_data(self, username: str) -> None:
        """Remove everything stored about a chess.com player (data-removal requests)."""
        with self._lock:
            for table in ("games", "months", "reports", "report_history", "players", "account_players",
                          "puzzle_progress"):
                self._conn.execute(f"DELETE FROM {table} WHERE username=?", (username,))
            self._conn.commit()

    # ----- reports ---------------------------------------------------------------------------
    def save_report(self, username: str, options_key: str, report: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO reports (username, options_key, report_json, created_at) VALUES (?,?,?,?)"
                " ON CONFLICT(username, options_key) DO UPDATE SET report_json=excluded.report_json,"
                " created_at=excluded.created_at",
                (username, options_key, json.dumps(report), time.time()),
            )
            self._conn.commit()

    def get_report(self, username: str, options_key: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT report_json FROM reports WHERE username=? AND options_key=?", (username, options_key)
            ).fetchone()
        return json.loads(r["report_json"]) if r else None

    def latest_report(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT report_json FROM reports WHERE username=? ORDER BY created_at DESC LIMIT 1", (username,)
            ).fetchone()
        return json.loads(r["report_json"]) if r else None

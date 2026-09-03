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

    def count_games(self, username: str) -> tuple[int, int]:
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS n, SUM(CASE WHEN analysis_json IS NOT NULL THEN 1 ELSE 0 END) AS a"
                " FROM games WHERE username=?",
                (username,),
            ).fetchone()
        return int(r["n"] or 0), int(r["a"] or 0)

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

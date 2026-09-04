import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from chess_analysis.config import settings
from chess_analysis.db import Database
from chess_analysis.engine import EnginePool
from chess_analysis.jobs import JobManager
from chess_analysis.main import create_app

ENGINE = settings.stockfish_path is not None


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.sqlite3")
    yield d
    d.close()


def run_job(db, pool, options):
    async def go():
        jm = JobManager(db, pool)
        job = jm.start("TestPlayer", options)
        while job.status not in ("done", "error", "cancelled"):
            await asyncio.sleep(0.05)
        return job
    return asyncio.run(go())


def test_pipeline_without_engine(db):
    pool = EnginePool(path=None, workers=1)
    job = run_job(db, pool, {"max_engine_games": 0, "depth": 8})
    assert job.status == "done", job.error
    rep = db.latest_report("testplayer")
    assert rep["overview"]["games_total"] == 70
    assert rep["overview"]["games_analyzed"] == 0
    assert rep["accuracy"]["available"] is False
    assert rep["time"]["available"] is True          # clocks work without an engine
    assert rep["openings"]["white"]["openings"]
    games, total = db.list_game_summaries("testplayer", limit=5)
    assert total == 70 and games[0]["analyzed"] is False
    wins, n_wins = db.list_game_summaries("testplayer", result="win", color="white", limit=200)
    assert n_wins == len(wins) and all(g["result"] == "win" and g["color"] == "white" for g in wins)
    assert db.history("testplayer")[0]["games_total"] == 70
    # cached months are skipped on the second run
    job2 = run_job(db, pool, {"max_engine_games": 0, "depth": 8})
    assert job2.status == "done"
    assert job2.progress["months_cached"] >= 1


@pytest.mark.skipif(not ENGINE, reason="stockfish not installed")
def test_pipeline_with_engine(db):
    pool = EnginePool(workers=2)
    try:
        job = run_job(db, pool, {"max_engine_games": 4, "depth": 8})
        assert job.status == "done", job.error
        rep = db.latest_report("testplayer")
        assert rep["overview"]["games_analyzed"] == 4
        acc = rep["accuracy"]
        assert acc["available"] and acc["overall"]["accuracy"] is not None
        assert sum(acc["class_counts"].values()) == acc["total_moves"]
        assert rep["tactics"]["available"]
        assert isinstance(rep["insights"], list)
        # a re-run at the same depth does no engine work
        job2 = run_job(db, pool, {"max_engine_games": 4, "depth": 8})
        assert job2.progress["engine_done"] == 4 and job2.progress["engine_total"] == 4
    finally:
        pool.close()


@pytest.mark.skipif(not ENGINE, reason="stockfish not installed")
def test_api_end_to_end(db):
    pool = EnginePool(workers=2)
    app = create_app(db=db, pool=pool)
    with TestClient(app) as client:
        assert client.get("/api/status").json()["engine"]["available"] is True
        r = client.post("/api/analyze", json={"username": "testplayer", "options": {"max_engine_games": 2, "depth": 8}})
        assert r.status_code == 200
        job_id = r.json()["id"]
        for _ in range(600):
            j = client.get(f"/api/jobs/{job_id}").json()
            if j["status"] in ("done", "error"):
                break
            import time
            time.sleep(0.1)
        assert j["status"] == "done", j
        rep = client.get("/api/report/testplayer").json()
        assert rep["player"]["username"] == "testplayer"
        listing = client.get("/api/players/testplayer/games?limit=3&analyzed=true").json()
        assert listing["total"] == 2 and len(listing["games"]) == 2
        gid = listing["games"][0]["id"]
        detail = client.get(f"/api/games/testplayer/{gid}").json()
        assert client.get("/api/players/testplayer/history").json()[0]["games_analyzed"] == 2
        assert rep["previous"] is None or "games_total" in rep["previous"]
        assert detail["analysis"]["moves"]
        assert detail["game"]["opponent"]
        assert client.get("/api/report/nobody").status_code == 404
        assert client.post("/api/analyze", json={"username": "bad name!"}).status_code == 400
        assert client.get("/").status_code == 200
        assert client.get("/api/players").json()[0]["username"] == "testplayer"


def test_unknown_player(db):
    pool = EnginePool(path=None, workers=1)
    job = run_job(db, pool, {"max_engine_games": 0})
    assert job.status == "done"

    async def go():
        jm = JobManager(db, pool)
        job = jm.start("ghost_user_404", {"max_engine_games": 0})
        while job.status not in ("done", "error", "cancelled"):
            await asyncio.sleep(0.05)
        return job
    j = asyncio.run(go())
    assert j.status == "error" and "no player" in j.error

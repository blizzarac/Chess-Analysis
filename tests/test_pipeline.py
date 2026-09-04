"""End-to-end tests against the offline fixtures: queue, worker, API, accounts and limits."""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "mock"
os.environ["CHESSCOM_MOCK_DIR"] = str(FIXTURES)
os.environ["AUTH_DEV_LINKS"] = "1"
os.environ["ADMIN_EMAILS"] = "admin@example.com"

from chess_analysis import worker  # noqa: E402
from chess_analysis.config import settings  # noqa: E402
from chess_analysis.db import Database  # noqa: E402
from chess_analysis.engine import EnginePool  # noqa: E402
from chess_analysis.jobs import clamp_options, enqueue  # noqa: E402

settings.mock_dir = FIXTURES
settings.auth_dev_links = True
settings.admin_emails = {"admin@example.com"}


@pytest.fixture
def db():
    d = Database(Path(tempfile.mkdtemp()) / "test.sqlite3")
    yield d
    d.close()


def run_job(db, pool, options):
    job = enqueue(db, "testplayer", options, settings.admin_caps)
    assert job["status"] == "queued"
    asyncio.run(worker.run_once(db, pool, "test-worker"))
    return db.get_job(job["id"])


def test_clamp_options_respects_caps():
    opts = clamp_options({"depth": 30, "max_engine_games": 5000, "max_months": None}, settings.anon_caps)
    assert opts["depth"] == settings.anon_caps.max_depth
    assert opts["max_engine_games"] == settings.anon_caps.max_engine_games
    assert opts["max_months"] == settings.anon_caps.max_months
    opts = clamp_options({"depth": 16, "max_months": 3}, settings.user_caps)
    assert opts["depth"] == 16 and opts["max_months"] == 3


def test_pipeline_without_engine(db):
    pool = EnginePool(path=None)
    job = run_job(db, pool, {"max_engine_games": 0, "depth": 8})
    assert job["status"] == "done", job["error"]
    rep = db.latest_report("testplayer")
    assert rep["overview"]["games_total"] == 70
    assert rep["overview"]["games_analyzed"] == 0
    assert rep["accuracy"]["available"] is False
    assert rep["time"]["available"] is True
    games, total = db.list_game_summaries("testplayer", limit=5)
    assert total == 70 and games[0]["analyzed"] is False
    wins, n_wins = db.list_game_summaries("testplayer", result="win", color="white", limit=200)
    assert n_wins == len(wins) and all(g["result"] == "win" and g["color"] == "white" for g in wins)
    assert db.history("testplayer")[0]["games_total"] == 70
    # second run: months are cached, nothing re-downloaded
    job2 = run_job(db, pool, {"max_engine_games": 0, "depth": 8})
    assert job2["status"] == "done" and job2["progress"]["months_cached"] == job2["progress"]["months_total"]


@pytest.mark.skipif(not settings.stockfish_path, reason="Stockfish not installed")
def test_pipeline_with_engine_and_resume(db):
    pool = EnginePool(workers=2)
    try:
        job = run_job(db, pool, {"max_engine_games": 4, "depth": 8})
        assert job["status"] == "done", job["error"]
        rep = db.latest_report("testplayer")
        assert rep["overview"]["games_analyzed"] == 4
        assert rep["accuracy"]["available"] and rep["accuracy"]["overall"]["accuracy"] is not None
        assert rep["puzzles"] and all(p["verified"] for p in rep["puzzles"])
        # a job interrupted mid-run is re-queued and completes on the next worker
        stale = enqueue(db, "testplayer", {"max_engine_games": 4, "depth": 8}, settings.admin_caps)
        claimed = db.claim_job("dead-worker")
        assert claimed["id"] == stale["id"]
        assert db.requeue_stale_jobs(older_than=-1) == 1
        asyncio.run(worker.run_once(db, pool, "live-worker"))
        assert db.get_job(stale["id"])["status"] == "done"
    finally:
        pool.close()


def test_cancel_and_dedupe(db):
    job = enqueue(db, "testplayer", {"max_engine_games": 0}, settings.anon_caps, client_ip="1.1.1.1")
    same = enqueue(db, "testplayer", {"max_engine_games": 0}, settings.anon_caps, client_ip="2.2.2.2")
    assert same["id"] == job["id"]
    assert db.request_cancel(job["id"])
    asyncio.run(worker.run_once(db, EnginePool(path=None), "w"))
    assert db.get_job(job["id"])["status"] == "cancelled"


@pytest.mark.skipif(not settings.stockfish_path, reason="Stockfish not installed")
def test_api_end_to_end(db):
    from fastapi.testclient import TestClient

    from chess_analysis.main import create_app

    pool = EnginePool(workers=2)
    app = create_app(db=db, pool=pool, inline_worker=False)
    try:
        with TestClient(app) as client:
            st = client.get("/api/status").json()
            assert st["account"] is None and st["caps"]["max_depth"] == settings.anon_caps.max_depth
            # anonymous options are clamped to the anonymous caps
            r = client.post("/api/analyze", json={"username": "testplayer", "options": {"max_engine_games": 2, "depth": 30}})
            assert r.status_code == 200, r.text
            job = r.json()
            assert job["status"] == "queued" and job["options"]["depth"] == settings.anon_caps.max_depth
            assert job["queue_position"] == 1
            asyncio.run(worker.run_once(db, pool, "w"))
            job = client.get(f"/api/jobs/{job['id']}").json()
            assert job["status"] == "done", job["error"]
            rep = client.get("/api/report/testplayer")
            assert rep.status_code == 200 and rep.headers.get("etag")
            assert client.get("/api/report/testplayer", headers={"If-None-Match": rep.headers["etag"]}).status_code == 304
            rep = rep.json()
            assert rep["overview"]["games_analyzed"] == 2
            listing = client.get("/api/players/testplayer/games?limit=3&analyzed=true").json()
            assert listing["total"] == 2
            gid = listing["games"][0]["id"]
            detail = client.get(f"/api/games/testplayer/{gid}").json()
            assert detail["analysis"]["engine"] is True
            assert client.get("/api/report/nobody_here").status_code == 404
            assert client.post("/api/analyze", json={"username": "bad name!"}).status_code == 400
            assert client.get("/api/players").status_code == 404  # no public listing of analysed players
            assert client.get("/api/me").status_code == 401
    finally:
        pool.close()


def test_accounts_quotas_and_progress(db):
    from fastapi.testclient import TestClient

    from chess_analysis.main import create_app

    app = create_app(db=db, pool=EnginePool(path=None), inline_worker=False)
    with TestClient(app) as client:
        assert client.post("/api/auth/request-link", json={"email": "not-an-email"}).status_code == 400
        r = client.post("/api/auth/request-link", json={"email": "Someone@Example.com"}).json()
        assert r["email"] == "someone@example.com" and r["dev_link"]
        token = r["dev_link"].split("/login/")[1]
        assert client.post("/api/auth/verify", json={"token": "nope"}).status_code == 400
        r = client.post("/api/auth/verify", json={"token": token})
        assert r.status_code == 200 and r.json()["account"]["email"] == "someone@example.com"
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 400  # single use
        st = client.get("/api/status").json()
        assert st["account"]["tier"] == "user" and st["caps"]["max_depth"] == settings.user_caps.max_depth
        # saved players and puzzle progress
        assert client.post("/api/me/players", json={"username": "TestPlayer"}).json()["players"][0]["username"] == "testplayer"
        client.put("/api/me/puzzles/testplayer", json={"progress": {"g:1": {"box": 2}}})
        assert client.get("/api/me/puzzles/testplayer").json()["progress"] == {"g:1": {"box": 2}}
        assert client.get("/api/me").json()["players"][0]["username"] == "testplayer"
        client.delete("/api/me/players/testplayer")
        assert client.get("/api/me").json()["players"] == []
        # daily quota
        settings.user_caps.jobs_per_day = 1
        try:
            assert client.post("/api/analyze", json={"username": "testplayer"}).status_code == 200
            db.update_job(db.active_job_for("testplayer")["id"], status="done")
            assert client.post("/api/analyze", json={"username": "otherplayer"}).status_code == 429
        finally:
            settings.user_caps.jobs_per_day = 20
        # admin endpoints are closed to normal users
        assert client.get("/api/admin/jobs").status_code == 403
        client.post("/api/auth/logout")
        assert client.get("/api/me").status_code == 401
        # admin
        token = client.post("/api/auth/request-link", json={"email": "admin@example.com"}).json()["dev_link"].split("/login/")[1]
        client.post("/api/auth/verify", json={"token": token})
        assert client.get("/api/admin/jobs").status_code == 200
        assert client.delete("/api/admin/players/testplayer").json()["deleted"] == "testplayer"

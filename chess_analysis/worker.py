"""The worker: claims queued jobs and runs them. `python -m chess_analysis.worker` for a
dedicated process; the web app can also run the same loop in-process (INLINE_WORKER=1)."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from .config import settings
from .db import Database
from .engine import EnginePool
from .jobs import JobRunner

log = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 120  # a job without a heartbeat for this long is put back in the queue
HEARTBEAT_SECONDS = 15


def worker_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:4]}"


async def run_once(db: Database, pool: EnginePool, worker_id: str) -> bool:
    """Run the next queued job, if any. Returns True when a job was processed."""
    row = db.claim_job(worker_id)
    if row is None:
        return False
    log.info("worker %s: job %s for %s", worker_id, row["id"], row["username"])
    runner = JobRunner(db, pool)
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                db.update_job(row["id"])  # touches the heartbeat column only

    hb = asyncio.create_task(heartbeat())
    try:
        result = await runner.run(row)
    finally:
        stop.set()
        hb.cancel()
    log.info("worker %s: job %s finished with %s", worker_id, row["id"], result.get("status"))
    return True


async def run_forever(db: Database, pool: EnginePool, worker_id: str | None = None, poll_seconds: float = 1.0,
                      stop_event: asyncio.Event | None = None) -> None:
    worker_id = worker_id or worker_name()
    requeued = db.requeue_stale_jobs(STALE_AFTER_SECONDS)
    if requeued:
        log.info("worker %s: re-queued %d interrupted job(s)", worker_id, requeued)
    last_sweep = asyncio.get_running_loop().time()
    while not (stop_event and stop_event.is_set()):
        try:
            worked = await run_once(db, pool, worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive whatever happens
            log.exception("worker %s: unexpected error", worker_id)
            worked = False
        now = asyncio.get_running_loop().time()
        if now - last_sweep > STALE_AFTER_SECONDS:
            db.requeue_stale_jobs(STALE_AFTER_SECONDS)
            last_sweep = now
        if not worked:
            try:
                if stop_event:
                    await asyncio.wait_for(stop_event.wait(), poll_seconds)
                else:
                    await asyncio.sleep(poll_seconds)
            except asyncio.TimeoutError:
                pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(settings.db_path)
    pool = EnginePool()
    log.info("engine: %s (%d workers)", pool.path or "not found", pool.workers)
    try:
        asyncio.run(run_forever(db, pool))
    except KeyboardInterrupt:
        pass
    finally:
        pool.close()
        db.close()


if __name__ == "__main__":
    main()

"""Client for the public chess.com API (https://www.chess.com/news/view/published-data-api).

All endpoints are unauthenticated. The API wants a descriptive User-Agent and prefers
serial requests, so archives are fetched one month at a time with a small retry loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .config import settings

log = logging.getLogger(__name__)

BASE = "https://api.chess.com/pub"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


class ChessComError(Exception):
    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


class PlayerNotFound(ChessComError):
    def __init__(self, username: str):
        super().__init__(f"chess.com has no player named '{username}'", 404)


def normalize_username(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise ChessComError("Usernames may only contain letters, digits, '_' and '-'.", 400)
    return username


class Throttle:
    """Process-wide minimum spacing between chess.com requests. The API is shared by every
    visitor of the site, so politeness here is what keeps the site's IP from being blocked."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock: asyncio.Lock | None = None

    async def wait(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


THROTTLE = Throttle(settings.chesscom_min_interval)


class ChessComClient:
    """Small async client. Reads from `mock_dir` when configured (tests, offline demos)."""

    def __init__(self, mock_dir: Path | None = None, user_agent: str | None = None, throttle: Throttle | None = None):
        self.mock_dir = mock_dir if mock_dir is not None else settings.mock_dir
        self.user_agent = user_agent or settings.user_agent
        self.throttle = throttle or THROTTLE
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ChessComClient":
        if self.mock_dir is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ----- transport -------------------------------------------------------------------------
    async def _get(self, path: str) -> dict[str, Any]:
        if self.mock_dir is not None:
            return self._mock_get(path)
        assert self._client is not None, "use `async with ChessComClient()`"
        url = f"{BASE}{path}"
        delay = 1.0
        for attempt in range(5):
            await self.throttle.wait()
            try:
                resp = await self._client.get(url)
            except httpx.HTTPError as exc:
                if attempt == 4:
                    raise ChessComError(f"Network error talking to chess.com: {exc}", 502) from exc
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 404:
                raise ChessComError(f"chess.com returned 404 for {path}", 404)
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt == 4:
                    raise ChessComError(f"chess.com returned {resp.status_code} for {path}", resp.status_code)
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                log.warning("chess.com %s for %s, retrying in %.1fs", resp.status_code, path, wait)
                await asyncio.sleep(wait)
                delay *= 2
                continue
            if resp.status_code >= 400:
                raise ChessComError(f"chess.com returned {resp.status_code} for {path}", resp.status_code)
            return resp.json()
        raise ChessComError("unreachable", 500)

    def _mock_get(self, path: str) -> dict[str, Any]:
        assert self.mock_dir is not None
        file = self.mock_dir / (path.strip("/").replace("/", "__") + ".json")
        if not file.exists():
            raise ChessComError(f"mock file missing for {path}", 404)
        return json.loads(file.read_text())

    # ----- endpoints -------------------------------------------------------------------------
    async def profile(self, username: str) -> dict[str, Any]:
        try:
            return await self._get(f"/player/{username}")
        except ChessComError as exc:
            if exc.status == 404:
                raise PlayerNotFound(username) from exc
            raise

    async def stats(self, username: str) -> dict[str, Any]:
        try:
            return await self._get(f"/player/{username}/stats")
        except ChessComError as exc:
            if exc.status == 404:
                return {}
            raise

    async def archives(self, username: str) -> list[str]:
        """Return the list of (year, month) archive URLs, oldest first."""
        try:
            data = await self._get(f"/player/{username}/games/archives")
        except ChessComError as exc:
            if exc.status == 404:
                raise PlayerNotFound(username) from exc
            raise
        return list(data.get("archives", []))

    async def month_games(self, username: str, year: int, month: int) -> list[dict[str, Any]]:
        data = await self._get(f"/player/{username}/games/{year:04d}/{month:02d}")
        return list(data.get("games", []))

    async def all_games(
        self,
        username: str,
        progress: Callable[[int, int, int], Awaitable[None]] | None = None,
        skip_months: set[tuple[int, int]] | None = None,
        max_months: int | None = None,
    ) -> list[dict[str, Any]]:
        """Download every archived month (newest first) unless already cached via `skip_months`."""
        archive_urls = await self.archives(username)
        months: list[tuple[int, int]] = []
        for url in archive_urls:
            m = re.search(r"/games/(\d{4})/(\d{2})$", url)
            if m:
                months.append((int(m.group(1)), int(m.group(2))))
        months.sort(reverse=True)
        if max_months is not None:
            months = months[:max_months]
        games: list[dict[str, Any]] = []
        total = len(months)
        for i, (year, month) in enumerate(months):
            if skip_months and (year, month) in skip_months:
                continue
            batch = await self.month_games(username, year, month)
            for g in batch:
                g["_archive_year"] = year
                g["_archive_month"] = month
            games.extend(batch)
            if progress is not None:
                await progress(i + 1, total, len(games))
        return games


def archive_months(archive_urls: list[str]) -> list[tuple[int, int]]:
    out = []
    for url in archive_urls:
        m = re.search(r"/games/(\d{4})/(\d{2})$", url)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return out

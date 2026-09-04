"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _find_stockfish() -> str | None:
    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).exists():
        return env
    for name in ("stockfish", "stockfish_16", "stockfish-ubuntu-x86-64-avx2"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in ("/usr/games/stockfish", "/usr/local/bin/stockfish", "/opt/homebrew/bin/stockfish"):
        if Path(candidate).exists():
            return candidate
    return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Caps:
    """Analysis limits for one tier of visitor."""
    max_engine_games: int
    max_depth: int
    max_months: int | None       # None = whole archive
    jobs_per_day: int


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CHESS_DATA_DIR", "data")))
    stockfish_path: str | None = field(default_factory=_find_stockfish)
    engine_workers: int = field(default_factory=lambda: _env_int("ENGINE_WORKERS", max(1, (os.cpu_count() or 2) - 1)))
    engine_hash_mb: int = field(default_factory=lambda: _env_int("ENGINE_HASH_MB", 64))
    # chess.com asks for an identifying User-Agent with contact info.
    user_agent: str = field(default_factory=lambda: os.environ.get(
        "CHESSCOM_USER_AGENT", "chess-analysis/0.2 (+https://github.com/blizzarac/Chess-Analysis)"))
    # Minimum seconds between two chess.com requests, site-wide (the worker is the only caller).
    chesscom_min_interval: float = field(default_factory=lambda: float(os.environ.get("CHESSCOM_MIN_INTERVAL", "0.4")))
    # When set, the chess.com client reads JSON files from this directory instead of the network.
    mock_dir: Path | None = field(default_factory=lambda: Path(os.environ["CHESSCOM_MOCK_DIR"])
                                  if os.environ.get("CHESSCOM_MOCK_DIR") else None)

    # ----- web / deployment ---------------------------------------------------------------
    base_url: str = field(default_factory=lambda: os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/"))
    inline_worker: bool = field(default_factory=lambda: _env_bool("INLINE_WORKER", True))
    trust_proxy: bool = field(default_factory=lambda: _env_bool("TRUST_PROXY", False))
    cookie_secure: bool = field(default_factory=lambda: _env_bool("COOKIE_SECURE", False))
    contact_email: str | None = field(default_factory=lambda: os.environ.get("CONTACT_EMAIL") or None)
    admin_emails: set[str] = field(default_factory=lambda: {
        e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()})

    # ----- accounts / email ---------------------------------------------------------------
    smtp_host: str | None = field(default_factory=lambda: os.environ.get("SMTP_HOST") or None)
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    smtp_user: str | None = field(default_factory=lambda: os.environ.get("SMTP_USER") or None)
    smtp_password: str | None = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD") or None)
    smtp_from: str = field(default_factory=lambda: os.environ.get("SMTP_FROM", "Chess Report <no-reply@localhost>"))
    smtp_starttls: bool = field(default_factory=lambda: _env_bool("SMTP_STARTTLS", True))
    # Return magic links in the API response instead of mailing them (local development only).
    auth_dev_links: bool = field(default_factory=lambda: _env_bool("AUTH_DEV_LINKS", False))
    login_token_minutes: int = 15
    session_days: int = 30

    # ----- limits --------------------------------------------------------------------------
    anon_caps: Caps = field(default_factory=lambda: Caps(
        max_engine_games=_env_int("ANON_MAX_GAMES", 30), max_depth=_env_int("ANON_MAX_DEPTH", 12),
        max_months=_env_int("ANON_MAX_MONTHS", 12), jobs_per_day=_env_int("ANON_JOBS_PER_DAY", 5)))
    user_caps: Caps = field(default_factory=lambda: Caps(
        max_engine_games=_env_int("USER_MAX_GAMES", 300), max_depth=_env_int("USER_MAX_DEPTH", 18),
        max_months=None, jobs_per_day=_env_int("USER_JOBS_PER_DAY", 20)))
    admin_caps: Caps = field(default_factory=lambda: Caps(max_engine_games=2000, max_depth=24, max_months=None,
                                                          jobs_per_day=1000))
    ip_requests_per_minute: int = field(default_factory=lambda: _env_int("IP_REQUESTS_PER_MINUTE", 120))
    login_links_per_hour: int = field(default_factory=lambda: _env_int("LOGIN_LINKS_PER_HOUR", 5))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chess_analysis.sqlite3"


settings = Settings()

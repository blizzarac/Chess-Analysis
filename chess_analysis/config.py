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


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CHESS_DATA_DIR", "data")))
    stockfish_path: str | None = field(default_factory=_find_stockfish)
    engine_workers: int = field(
        default_factory=lambda: int(os.environ.get("ENGINE_WORKERS", max(1, (os.cpu_count() or 2) - 1)))
    )
    engine_hash_mb: int = field(default_factory=lambda: int(os.environ.get("ENGINE_HASH_MB", "64")))
    # chess.com asks for an identifying User-Agent with contact info.
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "CHESSCOM_USER_AGENT", "chess-analysis/0.1 (+https://github.com/blizzarac/Chess-Analysis)"
        )
    )
    # When set, the chess.com client reads JSON files from this directory instead of the network.
    mock_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["CHESSCOM_MOCK_DIR"]) if os.environ.get("CHESSCOM_MOCK_DIR") else None
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chess_analysis.sqlite3"


settings = Settings()

"""A pool of Stockfish processes that evaluates every position of a game.

Each worker thread owns one engine process. `analyze_positions` returns one entry per
position: the evaluation *before* each move plus the final position, always from White's
point of view, along with the engine's preferred move and principal variation.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import chess
import chess.engine

from .config import settings

log = logging.getLogger(__name__)

MATE_CP = 100_000  # sentinel used when converting mate scores to centipawns


class EngineUnavailable(RuntimeError):
    pass


class EnginePool:
    def __init__(self, path: str | None = None, workers: int | None = None, hash_mb: int | None = None):
        self.path = path or settings.stockfish_path
        self.workers = max(1, workers or settings.engine_workers)
        self.hash_mb = hash_mb or settings.engine_hash_mb
        self._local = threading.local()
        self._engines: list[chess.engine.SimpleEngine] = []
        self._engines_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None

    @property
    def available(self) -> bool:
        return bool(self.path)

    def _engine(self) -> chess.engine.SimpleEngine:
        eng = getattr(self._local, "engine", None)
        if eng is None:
            if not self.path:
                raise EngineUnavailable("Stockfish binary not found; set STOCKFISH_PATH")
            eng = chess.engine.SimpleEngine.popen_uci(self.path)
            eng.configure({"Threads": 1, "Hash": self.hash_mb})
            self._local.engine = eng
            with self._engines_lock:
                self._engines.append(eng)
        return eng

    def executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="engine")
        return self._executor

    def close(self) -> None:
        with self._engines_lock:
            for eng in self._engines:
                try:
                    eng.quit()
                except Exception:  # pragma: no cover - best effort shutdown
                    pass
            self._engines.clear()
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None

    # ------------------------------------------------------------------------------------------
    def analyze_positions(
        self, moves_uci: list[str], depth: int = 14, start_fen: str | None = None, chess960: bool = False
    ) -> list[dict[str, Any]]:
        """Evaluate every position of a game (len(moves)+1 entries). Runs in the calling thread."""
        eng = self._engine()
        board = chess.Board(start_fen, chess960=chess960) if start_fen else chess.Board(chess960=chess960)
        limit = chess.engine.Limit(depth=depth)
        out: list[dict[str, Any]] = []

        def evaluate(b: chess.Board) -> dict[str, Any]:
            if b.is_checkmate():
                # side to move is mated: from white's POV it's -mate if white to move
                mate_in = 0
                cp = -MATE_CP if b.turn == chess.WHITE else MATE_CP
                return {"cp": cp, "mate": (-0 if b.turn == chess.WHITE else 0), "best": None, "pv": [], "mated": True}
            if b.is_stalemate() or b.is_insufficient_material() or b.can_claim_draw():
                return {"cp": 0, "mate": None, "best": None, "pv": [], "drawn": True}
            info = eng.analyse(b, limit)
            score = info.get("score")
            pv = [m.uci() for m in info.get("pv", [])]
            if score is None:
                return {"cp": 0, "mate": None, "best": pv[0] if pv else None, "pv": pv}
            white_score = score.white()
            mate = white_score.mate()
            if mate is not None:
                cp = MATE_CP - abs(mate) if mate > 0 else -MATE_CP + abs(mate)
            else:
                cp = white_score.score() or 0
            return {"cp": int(cp), "mate": mate, "best": pv[0] if pv else None, "pv": pv[:6]}

        out.append(evaluate(board))
        for uci in moves_uci:
            try:
                board.push_uci(uci)
            except ValueError:
                log.warning("illegal move %s in game; truncating analysis", uci)
                break
            out.append(evaluate(board))
        return out

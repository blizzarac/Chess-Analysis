"""Evaluation helpers: win probability, per-move accuracy and move classification.

The win-probability curve and the accuracy formula follow the ones Lichess publishes for
its game reports, so the numbers are comparable to what players already know.
"""
from __future__ import annotations

import math

CP_CLAMP = 1000
MATE_CP = 100_000

CLASSES = ("best", "excellent", "good", "inaccuracy", "mistake", "blunder")


def clamp_cp(cp: int | float) -> int:
    return int(max(-CP_CLAMP, min(CP_CLAMP, cp)))


def win_pct(cp_pov: int | float) -> float:
    """Win probability (0..100) for the side whose point of view `cp_pov` is from."""
    cp = clamp_cp(cp_pov)
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0)


def move_accuracy(win_before: float, win_after: float) -> float:
    """Accuracy of a move given the mover's win% before and after it."""
    drop = max(0.0, win_before - win_after)
    acc = 103.1668 * math.exp(-0.04354 * drop) - 3.1669
    return max(0.0, min(100.0, acc))


def classify(win_loss: float, played_best: bool, cp_loss: int) -> str:
    """Classify a move by the win-probability it gave away."""
    if played_best or (win_loss < 0.5 and cp_loss <= 5):
        return "best"
    if win_loss < 2.0:
        return "excellent"
    if win_loss < 5.0:
        return "good"
    if win_loss < 10.0:
        return "inaccuracy"
    if win_loss < 20.0:
        return "mistake"
    return "blunder"


def game_accuracy(per_move: list[float]) -> float | None:
    """Combine per-move accuracies: mean of the arithmetic and harmonic means, like Lichess.

    The harmonic mean punishes a single terrible move so that a game with one decisive
    blunder doesn't read as 'accurate'."""
    vals = [v for v in per_move if v is not None]
    if not vals:
        return None
    arith = sum(vals) / len(vals)
    eps = 1e-6
    harm = len(vals) / sum(1.0 / (v + eps) for v in vals)
    return round((arith + harm) / 2.0, 1)


def pov(cp_white: int, color: str) -> int:
    return cp_white if color == "white" else -cp_white


def format_eval(cp_white: int, mate: int | None) -> str:
    if mate is not None:
        return f"#{mate}" if mate > 0 else f"#-{abs(mate)}"
    if abs(cp_white) >= MATE_CP - 200:
        n = MATE_CP - abs(cp_white)
        return f"#{n}" if cp_white > 0 else f"#-{n}"
    return f"{cp_white / 100:+.2f}"

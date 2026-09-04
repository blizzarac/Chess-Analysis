"""Turn a chess.com game record (JSON + PGN) into a flat structure the analysis can consume."""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any

import chess
import chess.pgn

log = logging.getLogger(__name__)
CLOCK_RE = re.compile(r"\[%clk\s+(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\]")
HEADER_RE = re.compile(r'\[(\w+)\s+"([^"]*)"\]')

# chess.com result codes for a single side
WIN_CODES = {"win"}
DRAW_CODES = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
LOSS_CODES = {"checkmated", "resigned", "timeout", "lose", "abandoned", "kingofthehill", "threecheck",
              "bughousepartnerlose"}

RESULT_LABELS = {
    "win": "won",
    "checkmated": "checkmated",
    "agreed": "draw agreed",
    "repetition": "repetition",
    "timeout": "timeout",
    "resigned": "resigned",
    "stalemate": "stalemate",
    "lose": "lost",
    "insufficient": "insufficient material",
    "50move": "50-move rule",
    "abandoned": "abandoned",
    "timevsinsufficient": "timeout vs insufficient material",
}


@dataclass
class Move:
    ply: int                 # 1-based ply index
    san: str
    uci: str
    color: str               # "white" | "black" (side that played the move)
    fen_before: str
    fen_after: str
    clock: float | None      # clock reading after the move, seconds (None if no clock info)
    is_capture: bool
    is_check: bool
    piece: str               # piece type letter, e.g. "N"


@dataclass
class ParsedGame:
    id: str
    url: str
    end_time: int
    start_time: int | None
    time_class: str
    time_control: str
    base_seconds: float | None
    increment_seconds: float
    rated: bool
    rules: str
    white: str
    black: str
    white_rating: int | None
    black_rating: int | None
    white_result: str
    black_result: str
    player_color: str            # "white" | "black"
    player_result: str           # "win" | "draw" | "loss"
    player_result_code: str      # chess.com code for the player
    opponent_result_code: str
    result_known: bool           # False when chess.com used a result code this parser doesn't know
    termination: str             # normalized how-the-game-ended text
    eco: str | None
    opening_name: str | None
    opening_family: str | None
    moves: list[Move] = field(default_factory=list)
    final_fen: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    chesscom_accuracy: float | None = None

    @property
    def opponent(self) -> str:
        return self.black if self.player_color == "white" else self.white

    @property
    def player_rating(self) -> int | None:
        return self.white_rating if self.player_color == "white" else self.black_rating

    @property
    def opponent_rating(self) -> int | None:
        return self.black_rating if self.player_color == "white" else self.white_rating

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["opponent"] = self.opponent
        d["player_rating"] = self.player_rating
        d["opponent_rating"] = self.opponent_rating
        return d


def parse_time_control(tc: str | None) -> tuple[float | None, float]:
    """'180+2' -> (180, 2); '600' -> (600, 0); '1/86400' (daily) -> (None, 0)."""
    if not tc:
        return None, 0.0
    if "/" in tc:
        return None, 0.0
    if "+" in tc:
        base, inc = tc.split("+", 1)
        try:
            return float(base), float(inc)
        except ValueError:
            return None, 0.0
    try:
        return float(tc), 0.0
    except ValueError:
        return None, 0.0


def clock_seconds(comment: str) -> float | None:
    m = CLOCK_RE.search(comment or "")
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def opening_from_url(eco_url: str | None) -> tuple[str | None, str | None]:
    """Turn 'https://www.chess.com/openings/Sicilian-Defense-Najdorf-Variation-6.Be3' into
    ('Sicilian Defense: Najdorf Variation 6.Be3', 'Sicilian Defense')."""
    if not eco_url:
        return None, None
    slug = eco_url.rstrip("/").split("/")[-1]
    slug = re.sub(r"-\d+\.\.\..*$", lambda m: m.group(0), slug)  # keep move suffixes intact
    words = slug.split("-")
    # Family: words up to and including the first of Defense/Opening/Game/Gambit/Attack/System/Variation
    family_end = None
    for i, w in enumerate(words):
        if w in ("Defense", "Defence", "Opening", "Game", "Gambit", "Attack", "System", "Countergambit"):
            family_end = i
            break
    if family_end is None:
        # e.g. "Kings-Pawn-Opening" handled above; fallback to first two words
        family_end = min(1, len(words) - 1)
    family = " ".join(words[: family_end + 1])
    rest = words[family_end + 1:]
    name = family if not rest else f"{family}: {' '.join(rest)}"
    return name, family


def start_timestamp(headers: dict[str, str], end_time: int) -> int | None:
    """chess.com PGNs carry UTCDate/StartTime (and EndDate/EndTime); combine them into an epoch."""
    date = headers.get("UTCDate") or headers.get("Date")
    start = headers.get("StartTime")
    if not date or not start:
        return None
    try:
        dt = datetime.strptime(f"{date} {start}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    ts = int(dt.timestamp())
    if ts > end_time + 60:  # start clock rolled over midnight relative to the end date
        ts -= 86400
    return ts if ts <= end_time else None


def _normalize_termination(term: str | None, player_code: str, opp_code: str) -> str:
    code = player_code if player_code != "win" else opp_code
    return RESULT_LABELS.get(code, code or "unknown")


def parse_game(raw: dict[str, Any], username: str) -> ParsedGame | None:
    """Parse one chess.com game JSON record. Returns None when the game can't be parsed."""
    pgn_text = raw.get("pgn") or ""
    if not pgn_text:
        return None
    username_l = username.lower()
    white = raw.get("white", {}) or {}
    black = raw.get("black", {}) or {}
    w_user = (white.get("username") or "").lower()
    b_user = (black.get("username") or "").lower()
    if username_l == w_user:
        color = "white"
    elif username_l == b_user:
        color = "black"
    else:
        return None

    headers = dict(HEADER_RE.findall(pgn_text))
    rules = raw.get("rules") or "chess"
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return None
    if rules == "chess960":
        board = game.board()
    else:
        board = chess.Board(headers.get("FEN") or chess.STARTING_FEN) if "FEN" in headers else chess.Board()
        board = game.board() if game.headers.get("FEN") else board

    moves: list[Move] = []
    ply = 0
    for node in game.mainline():
        mv = node.move
        try:
            san = board.san(mv)
        except Exception:  # malformed PGN
            break
        ply += 1
        fen_before = board.fen()
        piece = board.piece_at(mv.from_square)
        is_capture = board.is_capture(mv)
        board.push(mv)
        moves.append(
            Move(
                ply=ply,
                san=san,
                uci=mv.uci(),
                color="white" if ply % 2 == 1 else "black",
                fen_before=fen_before,
                fen_after=board.fen(),
                clock=clock_seconds(node.comment),
                is_capture=is_capture,
                is_check=board.is_check(),
                piece=piece.symbol().upper() if piece else "?",
            )
        )

    base, inc = parse_time_control(raw.get("time_control"))
    w_code = white.get("result", "")
    b_code = black.get("result", "")
    p_code, o_code = (w_code, b_code) if color == "white" else (b_code, w_code)
    result_known = True
    if p_code in WIN_CODES:
        p_result = "win"
    elif p_code in DRAW_CODES:
        p_result = "draw"
    elif p_code in LOSS_CODES:
        p_result = "loss"
    else:
        result_known = False
        log.warning("unknown chess.com result code %r (opponent %r) in %s; counting as a loss",
                    p_code, o_code, raw.get("url"))
        p_result = "loss"

    eco_url = headers.get("ECOUrl") or raw.get("eco")
    opening_name, family = opening_from_url(eco_url)
    acc = None
    if raw.get("accuracies"):
        acc = raw["accuracies"].get(color)

    return ParsedGame(
        id=raw.get("_id") or raw.get("uuid") or raw.get("url") or "",
        url=raw.get("url", ""),
        end_time=int(raw.get("end_time") or 0),
        start_time=start_timestamp(headers, int(raw.get("end_time") or 0)),
        time_class=raw.get("time_class") or "unknown",
        time_control=raw.get("time_control") or "",
        base_seconds=base,
        increment_seconds=inc,
        rated=bool(raw.get("rated", False)),
        rules=rules,
        white=white.get("username", ""),
        black=black.get("username", ""),
        white_rating=white.get("rating"),
        black_rating=black.get("rating"),
        white_result=w_code,
        black_result=b_code,
        player_color=color,
        player_result=p_result,
        player_result_code=p_code,
        opponent_result_code=o_code,
        result_known=result_known,
        termination=_normalize_termination(headers.get("Termination"), p_code, o_code),
        eco=headers.get("ECO"),
        opening_name=opening_name,
        opening_family=family,
        moves=moves,
        final_fen=board.fen(),
        headers=headers,
        chesscom_accuracy=acc,
    )

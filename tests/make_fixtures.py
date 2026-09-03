"""Generate offline chess.com-shaped fixture data by letting Stockfish play weak games.

Usage: python tests/make_fixtures.py [out_dir]
Produces the JSON files `ChessComClient` reads when CHESSCOM_MOCK_DIR points at out_dir.
"""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chess_analysis.config import settings  # noqa: E402

PLAYER = "testplayer"
OPENINGS = [
    ("C50", "Italian-Game", ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]),
    ("C65", "Ruy-Lopez-Opening-Berlin-Defense", ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "g8f6"]),
    ("B20", "Sicilian-Defense", ["e2e4", "c7c5"]),
    ("B90", "Sicilian-Defense-Open-Najdorf-Variation", ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3", "a7a6"]),
    ("D02", "Queens-Pawn-Opening-London-System", ["d2d4", "d7d5", "g1f3", "g8f6", "c1f4"]),
    ("C02", "French-Defense-Advance-Variation", ["e2e4", "e7e6", "d2d4", "d7d5", "e4e5"]),
    ("B12", "Caro-Kann-Defense-Advance-Variation", ["e2e4", "c7c6", "d2d4", "d7d5", "e4e5"]),
    ("A40", "Queens-Pawn-Opening", ["d2d4"]),
    ("E60", "Kings-Indian-Defense", ["d2d4", "g8f6", "c2c4", "g7g6"]),
    ("C41", "Philidor-Defense", ["e2e4", "e7e5", "g1f3", "d7d6"]),
    ("A45", "Indian-Game", ["d2d4", "g8f6"]),
    ("B01", "Scandinavian-Defense", ["e2e4", "d7d5"]),
]
TIME_CONTROLS = [("blitz", "180"), ("blitz", "180+2"), ("blitz", "300"), ("rapid", "600"), ("rapid", "900+10"),
                 ("bullet", "60"), ("bullet", "120+1")]
OPPONENTS = ["magnus_fan", "rookie_ron", "pawnstorm", "queen_sac", "endgame_ed", "tilt_tom", "knightrider",
             "bishop_pair", "castle_carl", "fianchetto_fi"]


def clock_str(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:04.1f}"


def play_game(engine: chess.engine.SimpleEngine, rng: random.Random, player_color: chess.Color, skill: int,
              tc: str) -> dict:
    board = chess.Board()
    eco, slug, line = rng.choice(OPENINGS)
    base = float(tc.split("+")[0])
    inc = float(tc.split("+")[1]) if "+" in tc else 0.0
    clocks = {chess.WHITE: base, chess.BLACK: base}
    sans: list[str] = []
    result = None
    reason = None
    ply = 0
    player_blunder_rate = {chess.WHITE: 0.10, chess.BLACK: 0.13}[player_color]
    while not board.is_game_over(claim_draw=True) and ply < 160:
        mover = board.turn
        if ply < len(line):
            move = chess.Move.from_uci(line[ply])
            if move not in board.legal_moves:
                move = rng.choice(list(board.legal_moves))
        else:
            is_player = mover == player_color
            blunder_p = player_blunder_rate if is_player else 0.08
            if clocks[mover] < 15:
                blunder_p *= 2.5
            if rng.random() < blunder_p:
                move = rng.choice(list(board.legal_moves))
            else:
                engine.configure({"Skill Level": skill if is_player else rng.choice([2, 5, 8, 12])})
                move = engine.play(board, chess.engine.Limit(depth=4)).move
        san = board.san(move)
        # time usage: longer in the middlegame, quick in the opening, random spikes
        phase_factor = 0.3 if ply < 10 else (1.0 if ply < 60 else 0.6)
        spent = max(0.1, rng.gauss(base / 40 * phase_factor, base / 60))
        if rng.random() < 0.06:
            spent *= 4
        clocks[mover] = clocks[mover] - spent + inc
        if clocks[mover] <= 0:
            clocks[mover] = 0.0
            result = "0-1" if mover == chess.WHITE else "1-0"
            reason = "timeout"
            break
        board.push(move)
        ply += 1
        sans.append(f"{san} {{[%clk {clock_str(clocks[mover])}]}}")
        # resignation check every few plies when badly lost
        if ply > 30 and ply % 3 == 0:
            info = engine.analyse(board, chess.engine.Limit(depth=6))
            sc = info["score"].white().score(mate_score=10000)
            if sc is not None and abs(sc) > 700 and rng.random() < 0.5:
                result = "1-0" if sc > 0 else "0-1"
                reason = "resigned"
                break
    if result is None:
        if board.is_checkmate():
            result = "0-1" if board.turn == chess.WHITE else "1-0"
            reason = "checkmated"
        elif board.is_game_over(claim_draw=True):
            result = "1/2-1/2"
            reason = rng.choice(["repetition", "stalemate", "insufficient", "agreed"])
        else:
            result = "1/2-1/2"
            reason = "agreed"
    return {"board": board, "sans": sans, "result": result, "reason": reason, "eco": eco, "slug": slug,
            "plies": ply, "final_fen": board.fen()}


def result_codes(result: str, reason: str) -> tuple[str, str]:
    if result == "1/2-1/2":
        return reason, reason
    loser_code = reason if reason != "checkmated" else "checkmated"
    if result == "1-0":
        return "win", loser_code
    return loser_code, "win"


def build_pgn(g: dict, white: str, black: str, w_elo: int, b_elo: int, tc: str, end: datetime, codes) -> str:
    date = end.strftime("%Y.%m.%d")
    termination = {
        "1-0": f"{white} won", "0-1": f"{black} won", "1/2-1/2": "Game drawn",
    }[g["result"]]
    how = {"timeout": "on time", "resigned": "by resignation", "checkmated": "by checkmate",
           "repetition": "by repetition", "stalemate": "by stalemate", "insufficient": "by insufficient material",
           "agreed": "by agreement"}[g["reason"]]
    headers = [
        ("Event", "Live Chess"), ("Site", "Chess.com"), ("Date", date), ("Round", "-"), ("White", white),
        ("Black", black), ("Result", g["result"]), ("CurrentPosition", g["final_fen"]), ("Timezone", "UTC"),
        ("ECO", g["eco"]), ("ECOUrl", f"https://www.chess.com/openings/{g['slug']}"), ("UTCDate", date),
        ("UTCTime", end.strftime("%H:%M:%S")), ("WhiteElo", str(w_elo)), ("BlackElo", str(b_elo)),
        ("TimeControl", tc), ("Termination", f"{termination} {how}"), ("StartTime", end.strftime("%H:%M:%S")),
        ("EndDate", date), ("EndTime", end.strftime("%H:%M:%S")), ("Link", "https://www.chess.com/game/live/1"),
    ]
    text = "".join(f'[{k} "{v}"]\n' for k, v in headers) + "\n"
    parts = []
    for i, san in enumerate(g["sans"]):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}. {san}")
        else:
            parts.append(f"{i // 2 + 1}... {san}")
    text += " ".join(parts) + f" {g['result']}\n"
    return text


def main(out_dir: Path, n_games: int = 70, seed: int = 7) -> None:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = chess.engine.SimpleEngine.popen_uci(settings.stockfish_path)
    engine.configure({"Threads": 1, "Hash": 32})
    rating = 1180
    games_by_month: dict[tuple[int, int], list[dict]] = {}
    t = datetime(2026, 1, 3, 20, 15, tzinfo=timezone.utc).timestamp()
    for i in range(n_games):
        t += rng.choice([600, 900, 1800, 3600 * 5, 3600 * 20, 3600 * 50])
        # cluster some games late at night for the hour-of-day insight
        end = datetime.fromtimestamp(t, tz=timezone.utc)
        if rng.random() < 0.3:
            end = end.replace(hour=rng.choice([23, 0, 1]))
        player_color = chess.WHITE if i % 2 == 0 else chess.BLACK
        time_class, tc = rng.choice(TIME_CONTROLS)
        skill = 4 if time_class == "bullet" else 7
        g = play_game(engine, rng, player_color, skill, tc)
        opp = rng.choice(OPPONENTS)
        opp_rating = rating + rng.randint(-180, 180)
        codes = result_codes(g["result"], g["reason"])
        white, black = (PLAYER, opp) if player_color == chess.WHITE else (opp, PLAYER)
        w_elo, b_elo = (rating, opp_rating) if player_color == chess.WHITE else (opp_rating, rating)
        won = (g["result"] == "1-0") == (player_color == chess.WHITE) and g["result"] != "1/2-1/2"
        delta = 8 if won else (-8 if g["result"] != "1/2-1/2" else 0)
        rating += delta + rng.randint(-2, 2)
        pgn = build_pgn(g, white, black, w_elo, b_elo, tc, end, codes)
        record = {
            "url": f"https://www.chess.com/game/live/{100000 + i}",
            "pgn": pgn,
            "time_control": tc,
            "end_time": int(end.timestamp()),
            "rated": True,
            "tcn": "",
            "uuid": f"00000000-0000-0000-0000-{i:012d}",
            "initial_setup": chess.STARTING_FEN,
            "fen": g["final_fen"],
            "time_class": time_class,
            "rules": "chess",
            "eco": f"https://www.chess.com/openings/{g['slug']}",
            "white": {"rating": w_elo, "result": codes[0], "@id": f"https://api.chess.com/pub/player/{white}",
                      "username": white, "uuid": "w"},
            "black": {"rating": b_elo, "result": codes[1], "@id": f"https://api.chess.com/pub/player/{black}",
                      "username": black, "uuid": "b"},
        }
        if rng.random() < 0.5:
            record["accuracies"] = {"white": round(rng.uniform(60, 90), 1), "black": round(rng.uniform(60, 90), 1)}
        games_by_month.setdefault((end.year, end.month), []).append(record)
        print(f"game {i + 1}/{n_games}: {time_class} {tc} {g['result']} {g['reason']} plies={g['plies']}")
    engine.quit()

    archives = []
    for (y, m), games in sorted(games_by_month.items()):
        archives.append(f"https://api.chess.com/pub/player/{PLAYER}/games/{y:04d}/{m:02d}")
        (out_dir / f"player__{PLAYER}__games__{y:04d}__{m:02d}.json").write_text(json.dumps({"games": games}))
    (out_dir / f"player__{PLAYER}__games__archives.json").write_text(json.dumps({"archives": archives}))
    (out_dir / f"player__{PLAYER}.json").write_text(json.dumps({
        "username": PLAYER, "player_id": 1, "name": "Test Player", "url": f"https://www.chess.com/member/{PLAYER}",
        "country": "https://api.chess.com/pub/country/DE", "joined": 1600000000, "last_online": int(t),
        "status": "basic", "avatar": "",
    }))
    (out_dir / f"player__{PLAYER}__stats.json").write_text(json.dumps({
        "chess_blitz": {"last": {"rating": rating, "date": int(t), "rd": 40},
                        "best": {"rating": rating + 60, "date": int(t)},
                        "record": {"win": 30, "loss": 28, "draw": 4}},
        "chess_rapid": {"last": {"rating": rating + 90, "date": int(t), "rd": 60},
                        "best": {"rating": rating + 120, "date": int(t)},
                        "record": {"win": 12, "loss": 10, "draw": 2}},
        "chess_bullet": {"last": {"rating": rating - 120, "date": int(t), "rd": 70},
                         "best": {"rating": rating - 60, "date": int(t)},
                         "record": {"win": 8, "loss": 12, "draw": 1}},
        "tactics": {"highest": {"rating": 1650, "date": int(t)}, "lowest": {"rating": 900, "date": int(t)}},
    }))
    print(f"wrote {sum(len(v) for v in games_by_month.values())} games to {out_dir}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "fixtures" / "mock"
    main(out)

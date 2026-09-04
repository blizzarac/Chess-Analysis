import json
from pathlib import Path

import chess
import pytest

from chess_analysis.analysis import eval_utils as ev
from chess_analysis.analysis.game_analysis import annotate, endgame_type, non_pawn_piece_count
from chess_analysis.pgn_parse import opening_from_url, parse_game, parse_time_control

MOCK = Path(__file__).parent / "fixtures" / "mock"


def load_games():
    games = []
    for f in sorted(MOCK.glob("player__testplayer__games__20*.json")):
        games.extend(json.loads(f.read_text())["games"])
    return games


def test_time_control_parsing():
    assert parse_time_control("180+2") == (180.0, 2.0)
    assert parse_time_control("600") == (600.0, 0.0)
    assert parse_time_control("1/86400") == (None, 0.0)
    assert parse_time_control(None) == (None, 0.0)


def test_opening_from_url():
    name, family = opening_from_url("https://www.chess.com/openings/Sicilian-Defense-Najdorf-Variation-6.Be3")
    assert name == "Sicilian Defense: Najdorf Variation 6.Be3"
    assert family == "Sicilian Defense"
    assert opening_from_url(None) == (None, None)
    assert opening_from_url("https://www.chess.com/openings/Queens-Pawn-Opening-London-System")[1] == "Queens Pawn Opening"


def test_parse_game_fields():
    raw = load_games()[0]
    g = parse_game(raw, "TestPlayer")
    assert g is not None
    assert g.player_color in ("white", "black")
    assert g.player_result in ("win", "draw", "loss")
    assert g.moves and g.moves[0].ply == 1
    assert g.moves[0].clock is not None  # chess.com clock comments are parsed
    assert g.moves[0].fen_before == chess.STARTING_FEN
    assert g.moves[-1].fen_after == g.final_fen
    assert g.opening_name and g.opening_family
    assert parse_game(raw, "someone_else") is None


def test_win_pct_and_accuracy():
    assert ev.win_pct(0) == pytest.approx(50.0)
    assert ev.win_pct(1000) > 95
    assert ev.win_pct(-1000) < 5
    assert ev.move_accuracy(60, 60) == pytest.approx(100.0, abs=0.01)
    assert ev.move_accuracy(60, 30) < 40
    assert ev.classify(0.0, True, 0) == "best"
    assert ev.classify(3.0, False, 30) == "good"
    assert ev.classify(12.0, False, 120) == "mistake"
    assert ev.classify(25.0, False, 300) == "blunder"
    assert ev.game_accuracy([100, 100, 10]) < ev.game_accuracy([70, 70, 70])
    assert ev.format_eval(150, None) == "+1.50"
    assert ev.format_eval(-99997, -3) == "#-3"


def test_endgame_detection():
    b = chess.Board("8/8/4k3/8/8/4K3/4R3/8 w - - 0 1")
    assert non_pawn_piece_count(b) == 1
    assert endgame_type(b) == "rook"
    assert endgame_type(chess.Board("8/8/4k3/8/8/4K3/8/8 w - - 0 1")) == "pawn"
    assert endgame_type(chess.Board("8/8/4k3/8/8/4K3/4Q3/8 w - - 0 1")) == "queen"


def test_annotate_without_engine():
    g = parse_game(load_games()[0], "testplayer")
    ann = annotate(g, None)
    assert ann["engine"] is False
    assert ann["plies"] == len(g.moves)
    assert ann["has_clocks"] is True
    assert ann["moves"][0]["time_spent"] is not None
    assert {m["phase"] for m in ann["moves"]} <= {"opening", "middlegame", "endgame"}
    assert ann["moves"][0]["phase"] == "opening"


def test_annotate_with_fake_evals():
    """Feed a hand-made eval list: white blunders on move 2 and the annotation should say so."""
    raw = load_games()[0]
    g = parse_game(raw, "testplayer")
    n = len(g.moves)
    evals = [{"cp": 0, "mate": None, "best": m.uci, "pv": [m.uci]} for m in g.moves] + [{"cp": 0, "mate": None, "best": None, "pv": []}]
    # position after white's second move (index 3) is -400 for white
    evals[3] = {"cp": -400, "mate": None, "best": None, "pv": []}
    evals[2]["best"] = "h2h3" if g.moves[2].uci != "h2h3" else "h2h4"  # engine wanted something else
    ann = annotate(g, evals)
    assert ann["engine"] is True
    mv = ann["moves"][2]
    assert mv["color"] == "white" and mv["class"] == "blunder"
    assert mv["cp_loss"] == 400
    assert "collapsed" in mv["tags"]
    assert ann["white"]["classes"]["blunder"] >= 1
    assert len(ann["eval_curve"]) == n + 1


def test_start_time_and_book_and_phases():
    from chess_analysis.pgn_parse import start_timestamp
    from chess_analysis.analysis.game_analysis import annotate
    g = parse_game(load_games()[0], "testplayer")
    assert g.start_time is not None and g.start_time <= g.end_time
    assert start_timestamp({"UTCDate": "2026.01.03", "StartTime": "23:50:00"}, 1767486000) == 1767484200
    assert start_timestamp({}, 5) is None
    ann = annotate(g, None)
    assert ann["book"]["name"] and ann["start_fen"].startswith("rnbqkbnr")
    ms = ann["phases"]["middlegame_start_ply"]
    assert ms is None or 12 < ms <= 31
    assert all("premove" in m for m in ann["moves"])


def test_multipv_alternatives_flow_into_puzzles():
    from chess_analysis.analysis.game_analysis import annotate
    from chess_analysis.analysis.report import puzzle_candidates
    g = parse_game(load_games()[0], "testplayer")
    n = len(g.moves)
    evals = [{"cp": 0, "mate": None, "best": None, "pv": []} for _ in range(n + 1)]
    mv = g.moves[2]
    other = "h2h3" if mv.uci != "h2h3" else "h2h4"
    evals[2].update({"best": other, "pv": [other]})
    evals[3]["cp"] = -400
    # MultiPV says both `other` and a second move are fine, and the played move is not
    multipv = {"3": [{"uci": other, "cp": 0, "mate": None, "pv": [other]},
                     {"uci": "a2a4" if mv.uci != "a2a4" else "b2b3", "cp": -20, "mate": None, "pv": []},
                     {"uci": mv.uci, "cp": -400, "mate": None, "pv": []}]}
    ann = annotate(g, evals, multipv)
    m = ann["moves"][2]
    assert len(m["alternatives"]) == 2 and m["played_is_fine"] is False
    cands = puzzle_candidates(g, ann)
    assert cands and cands[0]["verified"] and len(cands[0]["accepted"]) == 2

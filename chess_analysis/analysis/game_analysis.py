"""Annotate a single game: per-move classification, phases, clocks and tactical tags."""
from __future__ import annotations

from typing import Any

import chess

from ..pgn_parse import ParsedGame
from . import book
from .eval_utils import MATE_CP, classify, clamp_cp, format_eval, game_accuracy, move_accuracy, pov, win_pct

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
OPENING_MIN_PLY = 12          # the opening never ends before move 6...
OPENING_MAX_PLY = 30          # ...and never lasts beyond move 15
OPENING_UNDEVELOPED_LIMIT = 2 # it ends once at most two minor pieces (both sides) are still at home
ENDGAME_PIECE_LIMIT = 6       # endgame when at most 6 non-pawn, non-king pieces remain in total
PREMOVE_SECONDS = 0.3         # a move played this fast was almost certainly a premove
ALTERNATIVE_WIN_MARGIN = 5.0  # alternatives within this many win% of the best move count as correct
ONLY_MOVE_GAP = 10.0          # an "only move" is this much better than the second-best one
MINOR_HOME = {chess.WHITE: {chess.B1: chess.KNIGHT, chess.G1: chess.KNIGHT, chess.C1: chess.BISHOP, chess.F1: chess.BISHOP},
              chess.BLACK: {chess.B8: chess.KNIGHT, chess.G8: chess.KNIGHT, chess.C8: chess.BISHOP, chess.F8: chess.BISHOP}}
TAG_LABELS = {
    "missed_mate": "Missed a forced mate",
    "allowed_mate": "Allowed a forced mate",
    "hung_piece": "Hung a piece",
    "lost_material": "Lost material",
    "missed_material": "Missed a material win",
    "bad_trade": "Bad trade",
    "walked_into_fork": "Walked into a fork",
    "threw_away_win": "Threw away a winning position",
    "collapsed": "Went from equal to lost",
    "time_trouble": "Played in time trouble",
    "rushed": "Rushed the decision",
    "missed_opponent_blunder": "Missed the opponent's blunder",
    "opening": "In the opening",
    "middlegame": "In the middlegame",
    "endgame": "In the endgame",
    "premature_resign": "Resigned a defensible position",
}


def undeveloped_minors(board: chess.Board) -> int:
    n = 0
    for color, squares in MINOR_HOME.items():
        for sq, pt in squares.items():
            piece = board.piece_at(sq)
            if piece is not None and piece.color == color and piece.piece_type == pt:
                n += 1
    return n


def opening_over(board: chess.Board, ply: int) -> bool:
    """The opening ends when development is (nearly) complete, bounded to moves 6-15."""
    if ply <= OPENING_MIN_PLY:
        return False
    if ply > OPENING_MAX_PLY:
        return True
    return undeveloped_minors(board) <= OPENING_UNDEVELOPED_LIMIT


def material(board: chess.Board, color: chess.Color) -> int:
    return sum(PIECE_VALUES[p] * len(board.pieces(p, color)) for p in PIECE_VALUES)


def non_pawn_piece_count(board: chess.Board) -> int:
    n = 0
    for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        n += len(board.pieces(pt, chess.WHITE)) + len(board.pieces(pt, chess.BLACK))
    return n


def endgame_type(board: chess.Board) -> str:
    """Name the endgame by the heaviest material on the board."""
    q = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.QUEEN, chess.BLACK))
    r = len(board.pieces(chess.ROOK, chess.WHITE)) + len(board.pieces(chess.ROOK, chess.BLACK))
    minors = sum(len(board.pieces(pt, c)) for pt in (chess.KNIGHT, chess.BISHOP) for c in (chess.WHITE, chess.BLACK))
    if q and not r and not minors:
        return "queen"
    if q:
        return "queen + pieces"
    if r and not minors:
        return "rook"
    if r and minors:
        return "rook + minor"
    if minors:
        bishops = len(board.pieces(chess.BISHOP, chess.WHITE)) + len(board.pieces(chess.BISHOP, chess.BLACK))
        knights = len(board.pieces(chess.KNIGHT, chess.WHITE)) + len(board.pieces(chess.KNIGHT, chess.BLACK))
        if bishops and not knights:
            return "bishop"
        if knights and not bishops:
            return "knight"
        return "minor piece"
    return "pawn"


def material_gain_along_pv(board: chess.Board, pv: list[str], mover: chess.Color, max_plies: int = 4) -> int:
    """Material swing (mover's point of view) after playing the first `max_plies` moves of a PV."""
    b = board.copy()
    start = material(b, mover) - material(b, not mover)
    for uci in pv[:max_plies]:
        try:
            b.push_uci(uci)
        except ValueError:
            break
    return (material(b, mover) - material(b, not mover)) - start


def is_fork(board_after_reply: chess.Board, reply_to: chess.Square, victim_color: chess.Color) -> bool:
    """After the opponent's reply landed on `reply_to`, does that piece attack 2+ valuable targets?"""
    piece = board_after_reply.piece_at(reply_to)
    if piece is None:
        return False
    attacked = board_after_reply.attacks(reply_to)
    targets = 0
    attacker_value = PIECE_VALUES[piece.piece_type]
    for sq in attacked:
        victim = board_after_reply.piece_at(sq)
        if victim is None or victim.color != victim_color:
            continue
        if victim.piece_type == chess.KING or PIECE_VALUES[victim.piece_type] > attacker_value:
            targets += 1
        elif PIECE_VALUES[victim.piece_type] >= attacker_value and not board_after_reply.is_attacked_by(victim_color, sq):
            targets += 1
    return targets >= 2


def time_trouble_threshold(base: float | None, inc: float) -> float | None:
    if base is None:
        return None
    if inc >= 5:
        return max(5.0, base * 0.05)
    return max(5.0, min(30.0, base * 0.10))


def annotate(game: ParsedGame, evals: list[dict[str, Any]] | None,
             multipv: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Build the full per-game annotation. `evals` may be None (no engine): only clocks/phases.

    `multipv` maps a ply (as a string) to the engine's top moves for the position before that
    ply; it is used to accept alternative solutions in puzzles and to flag only-moves."""
    multipv = multipv or {}
    n = len(game.moves)
    have_engine = evals is not None and len(evals) >= 2
    board = chess.Board(chess960=(game.rules == "chess960"))
    if game.moves and game.moves[0].fen_before != chess.STARTING_FEN:
        board = chess.Board(game.moves[0].fen_before, chess960=(game.rules == "chess960"))

    # --- phases -------------------------------------------------------------------------------
    middlegame_start = None
    endgame_start = None
    endgame_kind = None
    phases: list[str] = []
    for mv in game.moves:
        if endgame_start is None and non_pawn_piece_count(board) <= ENDGAME_PIECE_LIMIT:
            endgame_start = mv.ply
            endgame_kind = endgame_type(board)
        if middlegame_start is None and endgame_start is None and opening_over(board, mv.ply):
            middlegame_start = mv.ply
        if endgame_start is not None:
            phases.append("endgame")
        elif middlegame_start is not None:
            phases.append("middlegame")
        else:
            phases.append("opening")
        board.push_uci(mv.uci)
    # --- clocks -------------------------------------------------------------------------------
    base, inc = game.base_seconds, game.increment_seconds
    tt_threshold = time_trouble_threshold(base, inc)
    last_clock: dict[str, float | None] = {"white": base, "black": base}
    time_spent: list[float | None] = []
    for mv in game.moves:
        prev = last_clock[mv.color]
        if mv.clock is None or prev is None:
            time_spent.append(None)
        else:
            spent = prev - mv.clock + inc
            time_spent.append(max(0.0, spent))
        if mv.clock is not None:
            last_clock[mv.color] = mv.clock

    # --- per-move engine annotation -----------------------------------------------------------
    moves_out: list[dict[str, Any]] = []
    board = chess.Board(game.moves[0].fen_before, chess960=(game.rules == "chess960")) if game.moves else chess.Board()
    prev_player_move: dict[str, Any] | None = None
    prev_move: dict[str, Any] | None = None
    for i, mv in enumerate(game.moves):
        entry: dict[str, Any] = {
            "ply": mv.ply,
            "san": mv.san,
            "uci": mv.uci,
            "color": mv.color,
            "fen": mv.fen_after,
            "clock": mv.clock,
            "time_spent": None if time_spent[i] is None else round(time_spent[i], 1),
            "phase": phases[i],
            "premove": time_spent[i] is not None and time_spent[i] < PREMOVE_SECONDS,
            "piece": mv.piece,
            "capture": mv.is_capture,
            "check": mv.is_check,
            "tags": [],
        }
        if have_engine and i + 1 < len(evals):
            before, after = evals[i], evals[i + 1]
            cp_b_w, cp_a_w = before["cp"], after["cp"]
            cp_b, cp_a = pov(cp_b_w, mv.color), pov(cp_a_w, mv.color)
            wb, wa = win_pct(cp_b), win_pct(cp_a)
            win_loss = max(0.0, wb - wa)
            cp_loss = max(0, clamp_cp(cp_b) - clamp_cp(cp_a))
            played_best = before.get("best") == mv.uci
            cls = classify(win_loss, played_best, cp_loss)
            acc = move_accuracy(wb, wa)
            best_san = None
            pv_san: list[str] = []
            if before.get("best"):
                try:
                    best_san = board.san(chess.Move.from_uci(before["best"]))
                    b2 = board.copy()
                    for u in before.get("pv", [])[:6]:
                        m2 = chess.Move.from_uci(u)
                        pv_san.append(b2.san(m2))
                        b2.push(m2)
                except (ValueError, AssertionError):
                    pass
            entry.update(
                {
                    "eval": clamp_cp(cp_a_w),
                    "eval_text": format_eval(cp_a_w, after.get("mate")),
                    "eval_before": clamp_cp(cp_b_w),
                    "mate": after.get("mate"),
                    "win_before": round(wb, 1),
                    "win_after": round(wa, 1),
                    "win_loss": round(win_loss, 1),
                    "cp_loss": cp_loss,
                    "accuracy": round(acc, 1),
                    "class": cls,
                    "best": before.get("best"),
                    "best_san": best_san,
                    "pv": pv_san,
                }
            )
            # ---- tactical tags (why was it bad?) -------------------------------------------
            tags: list[str] = []
            if cls in ("mistake", "blunder", "inaccuracy"):
                mover = chess.WHITE if mv.color == "white" else chess.BLACK
                best_mate = before.get("mate")
                best_mates_for_mover = best_mate is not None and ((best_mate > 0) == (mover == chess.WHITE))
                after_mate = after.get("mate")
                after_mated = after_mate is not None and ((after_mate > 0) != (mover == chess.WHITE)) and not after.get("mated")
                if best_mates_for_mover and not (after_mate is not None and (after_mate > 0) == (mover == chess.WHITE)):
                    tags.append("missed_mate")
                if after_mated and not (best_mate is not None and (best_mate > 0) != (mover == chess.WHITE)):
                    tags.append("allowed_mate")
                best_gain = material_gain_along_pv(board, before.get("pv", []), mover) if before.get("pv") else 0
                board_after = board.copy()
                board_after.push_uci(mv.uci)
                opp_gain = material_gain_along_pv(board_after, after.get("pv", []), not mover) if after.get("pv") else 0
                if opp_gain >= 2 and cls in ("mistake", "blunder"):
                    reply = after.get("pv", [None])[0]
                    if reply:
                        rm = chess.Move.from_uci(reply)
                        if board_after.is_capture(rm):
                            tags.append("hung_piece" if not mv.is_capture else "bad_trade")
                        else:
                            b3 = board_after.copy()
                            b3.push(rm)
                            if is_fork(b3, rm.to_square, mover):
                                tags.append("walked_into_fork")
                            else:
                                tags.append("lost_material")
                    else:
                        tags.append("lost_material")
                if best_gain >= 2 and not tags and cls in ("mistake", "blunder"):
                    tags.append("missed_material")
                if wb >= 75 and wa < 60:
                    tags.append("threw_away_win")
                elif 40 <= wb <= 60 and wa < 25:
                    tags.append("collapsed")
                if tt_threshold is not None and mv.clock is not None and mv.clock < tt_threshold:
                    tags.append("time_trouble")
                elif (time_spent[i] is not None and base and base >= 180 and PREMOVE_SECONDS <= time_spent[i] < 2.0
                      and cls in ("mistake", "blunder")):
                    tags.append("rushed")
                if (
                    prev_move is not None
                    and prev_move.get("color") != mv.color
                    and prev_move.get("win_loss", 0) >= 10
                    and win_loss >= 10
                ):
                    tags.append("missed_opponent_blunder")
                tags.append(phases[i])
            entry["tags"] = tags
            # ---- MultiPV verification (puzzle candidates only) -------------------------------
            alts = multipv.get(str(mv.ply))
            if alts:
                mover = chess.WHITE if mv.color == "white" else chess.BLACK
                ranked = []
                for alt in alts:
                    try:
                        alt_san = board.san(chess.Move.from_uci(alt["uci"]))
                    except (ValueError, AssertionError):
                        continue
                    ranked.append({"uci": alt["uci"], "san": alt_san, "win": round(win_pct(pov(alt["cp"], mv.color)), 1)})
                if ranked:
                    top = ranked[0]["win"]
                    accepted = [a for a in ranked if a["win"] >= top - ALTERNATIVE_WIN_MARGIN]
                    entry["alternatives"] = accepted
                    entry["only_move"] = len(ranked) > 1 and (top - ranked[1]["win"]) >= ONLY_MOVE_GAP
                    played_alt = next((a for a in ranked if a["uci"] == mv.uci), None)
                    entry["played_is_fine"] = played_alt is not None and played_alt["win"] >= top - ALTERNATIVE_WIN_MARGIN
        board.push_uci(mv.uci)
        prev_move = entry
        if mv.color == game.player_color:
            prev_player_move = entry
        moves_out.append(entry)

    # --- game-level summaries ---------------------------------------------------------------
    result: dict[str, Any] = {
        "id": game.id,
        "engine": have_engine,
        "player_color": game.player_color,
        "start_fen": game.moves[0].fen_before if game.moves else chess.STARTING_FEN,
        "phases": {
            "middlegame_start_ply": middlegame_start,
            "endgame_start_ply": endgame_start,
            "endgame_type": endgame_kind,
        },
        "moves": moves_out,
        "plies": n,
        "has_clocks": any(m.clock is not None for m in game.moves),
        "time_trouble_threshold": tt_threshold,
        "start_fen": game.moves[0].fen_before if game.moves else chess.STARTING_FEN,
        "book": book.lookup([m.san for m in game.moves], game.player_color) if game.rules == "chess" else None,
    }
    # first time the player went wrong in the opening (for the per-opening "typical mistake" stat)
    result["first_error"] = None
    if have_engine:
        for idx, m in enumerate(moves_out):
            if m["color"] == game.player_color and m.get("class") in ("inaccuracy", "mistake", "blunder"):
                if m["phase"] != "opening":
                    break
                result["first_error"] = {"ply": m["ply"], "san": m["san"], "best_san": m.get("best_san"),
                                         "class": m["class"], "win_loss": m.get("win_loss"),
                                         "fen": moves_out[idx - 1]["fen"] if idx else result["start_fen"],
                                         "uci": m["uci"], "best": m.get("best")}
                break
    for color in ("white", "black"):
        col_moves = [m for m in moves_out if m["color"] == color]
        summary: dict[str, Any] = {"moves": len(col_moves)}
        if have_engine:
            accs = [m["accuracy"] for m in col_moves if "accuracy" in m]
            cpl = [m["cp_loss"] for m in col_moves if "cp_loss" in m]
            summary["accuracy"] = game_accuracy(accs)
            summary["acpl"] = round(sum(cpl) / len(cpl), 1) if cpl else None
            counts = {c: 0 for c in ("best", "excellent", "good", "inaccuracy", "mistake", "blunder")}
            for m in col_moves:
                if "class" in m:
                    counts[m["class"]] += 1
            summary["classes"] = counts
            for phase in ("opening", "middlegame", "endgame"):
                ph = [m["cp_loss"] for m in col_moves if m["phase"] == phase and "cp_loss" in m]
                summary[f"acpl_{phase}"] = round(sum(ph) / len(ph), 1) if ph else None
                summary[f"moves_{phase}"] = len(ph)
        spent = [m["time_spent"] for m in col_moves if m["time_spent"] is not None]
        summary["time_used"] = round(sum(spent), 1) if spent else None
        result[color] = summary

    # eval curve (white POV, clamped) for the chart
    if have_engine:
        result["eval_curve"] = [clamp_cp(e["cp"]) for e in evals[: n + 1]]
        # eval at move 10 / endgame start, useful for opening & endgame aggregates
        idx10 = min(20, n)
        result["eval_after_opening"] = clamp_cp(evals[idx10]["cp"]) if idx10 < len(evals) else None
        if endgame_start is not None and endgame_start - 1 < len(evals):
            result["eval_at_endgame"] = clamp_cp(evals[endgame_start - 1]["cp"])
        else:
            result["eval_at_endgame"] = None
        # critical moments: largest win% swings by either side
        swings = sorted(
            (m for m in moves_out if "win_loss" in m and m["win_loss"] >= 10),
            key=lambda m: -m["win_loss"],
        )
        result["critical_moments"] = [
            {"ply": m["ply"], "san": m["san"], "color": m["color"], "win_loss": m["win_loss"], "class": m["class"],
             "uci": m["uci"], "best": m.get("best"), "best_san": m.get("best_san"),
             "fen": moves_out[m["ply"] - 2]["fen"] if m["ply"] > 1 else result["start_fen"]}
            for m in swings[:5]
        ]
        # premature resignation: resigned while eval was still >= -1.5 for the player
        if game.player_result_code == "resigned" and evals:
            final_cp = pov(evals[min(n, len(evals) - 1)]["cp"], game.player_color)
            if final_cp >= -150:
                result["premature_resign"] = True
    return result

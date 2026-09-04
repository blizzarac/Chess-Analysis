"""Aggregate every parsed game (and its engine annotation) into the improvement report."""
from __future__ import annotations

import statistics
import time

import chess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from ..pgn_parse import ParsedGame
from .eval_utils import win_pct, pov
from .game_analysis import TAG_LABELS
from .insights import build_insights, build_training_plan

PHASES = ("opening", "middlegame", "endgame")
CLASSES = ("best", "excellent", "good", "inaccuracy", "mistake", "blunder")
TIME_CLASS_ORDER = ["bullet", "blitz", "rapid", "daily"]


def _score(wins: int, draws: int, losses: int) -> float | None:
    n = wins + draws + losses
    return round(100.0 * (wins + 0.5 * draws) / n, 1) if n else None


def _mean(xs: Iterable[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _wdl(games: list[ParsedGame]) -> dict[str, Any]:
    w = sum(1 for g in games if g.player_result == "win")
    d = sum(1 for g in games if g.player_result == "draw")
    l = len(games) - w - d
    return {"games": len(games), "wins": w, "draws": d, "losses": l, "score": _score(w, d, l)}


def _player_moves(ann: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in ann["moves"] if m["color"] == ann["player_color"]]


def _opp_moves(ann: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in ann["moves"] if m["color"] != ann["player_color"]]


def _month_key(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")


# ----------------------------------------------------------------------------------------------
def overview_section(games: list[ParsedGame], analyzed: list[tuple[ParsedGame, dict]], stats: dict) -> dict[str, Any]:
    by_tc: dict[str, Any] = {}
    for tc in sorted({g.time_class for g in games}, key=lambda t: TIME_CLASS_ORDER.index(t) if t in TIME_CLASS_ORDER else 9):
        tc_games = [g for g in games if g.time_class == tc]
        entry = _wdl(tc_games)
        latest = max(tc_games, key=lambda g: g.end_time)
        entry["rating_now"] = latest.player_rating
        ratings = [g.player_rating for g in tc_games if g.player_rating]
        entry["rating_peak"] = max(ratings) if ratings else None
        entry["rating_low"] = min(ratings) if ratings else None
        cc = stats.get(f"chess_{tc}", {})
        entry["chesscom_rating"] = (cc.get("last") or {}).get("rating")
        entry["chesscom_best"] = (cc.get("best") or {}).get("rating")
        entry["as_white"] = _wdl([g for g in tc_games if g.player_color == "white"])
        entry["as_black"] = _wdl([g for g in tc_games if g.player_color == "black"])
        by_tc[tc] = entry
    by_color = {c: _wdl([g for g in games if g.player_color == c]) for c in ("white", "black")}
    ordered = sorted(games, key=lambda g: g.end_time)
    # streaks
    longest_win = longest_loss = cur = 0
    cur_kind = None
    for g in ordered:
        if g.player_result == cur_kind and cur_kind in ("win", "loss"):
            cur += 1
        else:
            cur_kind = g.player_result
            cur = 1 if cur_kind in ("win", "loss") else 0
        if cur_kind == "win":
            longest_win = max(longest_win, cur)
        elif cur_kind == "loss":
            longest_loss = max(longest_loss, cur)
    plies = [len(g.moves) for g in games if g.moves]
    return {
        "games_total": len(games),
        "games_analyzed": len(analyzed),
        "rated": sum(1 for g in games if g.rated),
        "first_game": ordered[0].end_time if ordered else None,
        "last_game": ordered[-1].end_time if ordered else None,
        "all": _wdl(games),
        "by_time_class": by_tc,
        "by_color": by_color,
        "streaks": {"longest_win": longest_win, "longest_loss": longest_loss,
                    "current": cur if cur_kind in ("win", "loss") else 0, "current_kind": cur_kind},
        "avg_game_length_moves": round(statistics.mean(plies) / 2, 1) if plies else None,
        "puzzle_rating": ((stats.get("tactics") or {}).get("highest") or {}).get("rating"),
    }


def ratings_section(games: list[ParsedGame]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for g in sorted(games, key=lambda g: g.end_time):
        if g.player_rating and g.rated:
            out.setdefault(g.time_class, []).append({"t": g.end_time, "rating": g.player_rating,
                                                     "result": g.player_result})
    # downsample long series to ~400 points
    for tc, series in out.items():
        if len(series) > 400:
            step = len(series) / 400
            out[tc] = [series[int(i * step)] for i in range(400)] + [series[-1]]
    return out


def results_section(games: list[ParsedGame]) -> dict[str, Any]:
    wins = [g for g in games if g.player_result == "win"]
    losses = [g for g in games if g.player_result == "loss"]
    draws = [g for g in games if g.player_result == "draw"]
    term_w = Counter(g.termination for g in wins)
    term_l = Counter(g.termination for g in losses)
    term_d = Counter(g.termination for g in draws)

    # vs rating difference buckets
    buckets = [(-10_000, -200, "200+ lower"), (-200, -100, "100-200 lower"), (-100, -25, "25-100 lower"),
               (-25, 25, "even"), (25, 100, "25-100 higher"), (100, 200, "100-200 higher"), (200, 10_000, "200+ higher")]
    vs_rating = []
    for lo, hi, label in buckets:
        gs = [g for g in games if g.player_rating and g.opponent_rating and lo <= (g.opponent_rating - g.player_rating) < hi]
        vs_rating.append({"bucket": label, **_wdl(gs)})

    by_hour = []
    for h in range(24):
        gs = [g for g in games if datetime.fromtimestamp(g.end_time, tz=timezone.utc).hour == h]
        by_hour.append({"hour": h, **_wdl(gs)})
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_weekday = []
    for i, name in enumerate(weekdays):
        gs = [g for g in games if datetime.fromtimestamp(g.end_time, tz=timezone.utc).weekday() == i]
        by_weekday.append({"weekday": name, **_wdl(gs)})

    months: dict[str, list[ParsedGame]] = defaultdict(list)
    for g in games:
        months[_month_key(g.end_time)].append(g)
    by_month = [{"month": k, **_wdl(v)} for k, v in sorted(months.items())]

    # tilt: performance in games started shortly after a loss
    ordered = sorted(games, key=lambda g: g.end_time)
    after_loss, baseline, rematch_after_loss = [], [], []
    for prev, cur in zip(ordered, ordered[1:]):
        start = cur.start_time if cur.start_time else cur.end_time - len(cur.moves) * 5
        gap = start - prev.end_time
        if prev.player_result == "loss" and gap < 20 * 60:
            after_loss.append(cur)
            if cur.opponent.lower() == prev.opponent.lower():
                rematch_after_loss.append(cur)
        else:
            baseline.append(cur)
    # sessions: games within 30 min of each other; performance by game number in a session
    session_pos: dict[int, list[ParsedGame]] = defaultdict(list)
    pos = 0
    last_t = None
    for g in ordered:
        start = g.start_time if g.start_time else g.end_time - len(g.moves) * 5
        if last_t is None or start - last_t > 30 * 60:
            pos = 1
        else:
            pos += 1
        last_t = g.end_time
        session_pos[min(pos, 8)].append(g)
    session_curve = [{"game_no": k if k < 8 else "8+", **_wdl(v)} for k, v in sorted(session_pos.items())]

    return {
        "termination_wins": dict(term_w.most_common()),
        "termination_losses": dict(term_l.most_common()),
        "termination_draws": dict(term_d.most_common()),
        "vs_rating": vs_rating,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "by_month": by_month,
        "tilt": {
            "after_loss": _wdl(after_loss),
            "baseline": _wdl(baseline),
            "rematch_after_loss": _wdl(rematch_after_loss),
        },
        "session_curve": session_curve,
        "game_length": {
            "short_under_20": _wdl([g for g in games if len(g.moves) < 40]),
            "medium_20_40": _wdl([g for g in games if 40 <= len(g.moves) < 80]),
            "long_over_40": _wdl([g for g in games if len(g.moves) >= 80]),
        },
    }


def accuracy_section(analyzed: list[tuple[ParsedGame, dict]]) -> dict[str, Any]:
    if not analyzed:
        return {"available": False}
    per_game = []
    for g, ann in analyzed:
        me = ann[g.player_color]
        per_game.append((g, me))
    class_counts = Counter()
    total_moves = 0
    for g, me in per_game:
        for c in CLASSES:
            class_counts[c] += me["classes"][c]
        total_moves += me["moves"]

    def group(gs: list[tuple[ParsedGame, dict]]) -> dict[str, Any]:
        return {
            "games": len(gs),
            "accuracy": _mean(me["accuracy"] for _, me in gs),
            "acpl": _mean(me["acpl"] for _, me in gs),
            "blunders_per_game": round(sum(me["classes"]["blunder"] for _, me in gs) / len(gs), 2) if gs else None,
            "mistakes_per_game": round(sum(me["classes"]["mistake"] for _, me in gs) / len(gs), 2) if gs else None,
        }

    by_phase = {}
    phase_examples: dict[str, dict[str, Any]] = {}
    for g, ann in analyzed:
        moves = ann["moves"]
        for i, m in enumerate(moves):
            if m["color"] == g.player_color and m.get("class") == "blunder" and m["phase"] not in phase_examples:
                phase_examples[m["phase"]] = {"game_id": g.id, "ply": m["ply"], "san": m["san"], "uci": m["uci"],
                                              "best": m.get("best"), "best_san": m.get("best_san"),
                                              "fen": moves[i - 1]["fen"] if i else ann.get("start_fen"),
                                              "side": g.player_color, "win_loss": m.get("win_loss"),
                                              "opponent": g.opponent, "date": g.end_time}
    for ph in PHASES:
        vals = [me[f"acpl_{ph}"] for _, me in per_game if me.get(f"acpl_{ph}") is not None]
        moves = sum(me.get(f"moves_{ph}", 0) for _, me in per_game)
        # blunders in that phase
        bl = 0
        mi = 0
        for g, ann in analyzed:
            for m in _player_moves(ann):
                if m.get("phase") == ph and m.get("class") == "blunder":
                    bl += 1
                elif m.get("phase") == ph and m.get("class") == "mistake":
                    mi += 1
        by_phase[ph] = {
            "acpl": _mean(vals),
            "moves": moves,
            "blunders": bl,
            "mistakes": mi,
            "blunder_rate_per_100": round(100 * bl / moves, 2) if moves else None,
            "games_reaching": len(vals),
        }

    by_move_number: list[dict[str, Any]] = []
    bucket: dict[int, list[int]] = defaultdict(list)
    for g, ann in analyzed:
        for m in _player_moves(ann):
            if "cp_loss" in m:
                move_no = (m["ply"] + 1) // 2
                bucket[min(move_no, 60)].append(m["cp_loss"])
    for mn in sorted(bucket):
        xs = bucket[mn]
        by_move_number.append({"move": mn, "avg_cp_loss": round(sum(xs) / len(xs), 1), "n": len(xs)})

    months: dict[str, list[tuple[ParsedGame, dict]]] = defaultdict(list)
    for g, me in per_game:
        months[_month_key(g.end_time)].append((g, me))
    trend = [{"month": k, **group(v)} for k, v in sorted(months.items())]

    opp_group = group([(g, ann[("black" if g.player_color == "white" else "white")]) for g, ann in analyzed])

    # eval swings: how often does the player go from winning to not winning
    thrown = 0
    winning_games = 0
    for g, ann in analyzed:
        curve = ann.get("eval_curve") or []
        pov_curve = [pov(c, g.player_color) for c in curve]
        if any(c >= 300 for c in pov_curve):
            winning_games += 1
            if g.player_result != "win":
                thrown += 1
    swindled = 0
    losing_games = 0
    for g, ann in analyzed:
        curve = ann.get("eval_curve") or []
        pov_curve = [pov(c, g.player_color) for c in curve]
        if any(c <= -300 for c in pov_curve):
            losing_games += 1
            if g.player_result != "loss":
                swindled += 1

    return {
        "available": True,
        "overall": group(per_game),
        "opponents": opp_group,
        "by_color": {c: group([(g, me) for g, me in per_game if g.player_color == c]) for c in ("white", "black")},
        "by_time_class": {tc: group([(g, me) for g, me in per_game if g.time_class == tc])
                          for tc in sorted({g.time_class for g, _ in per_game})},
        "by_result": {r: group([(g, me) for g, me in per_game if g.player_result == r]) for r in ("win", "draw", "loss")},
        "by_phase": by_phase,
        "phase_examples": phase_examples,
        "class_counts": dict(class_counts),
        "total_moves": total_moves,
        "by_move_number": by_move_number,
        "trend": trend,
        "winning_positions": {"games": winning_games, "not_won": thrown,
                              "conversion_pct": round(100 * (winning_games - thrown) / winning_games, 1) if winning_games else None},
        "losing_positions": {"games": losing_games, "saved": swindled,
                             "save_pct": round(100 * swindled / losing_games, 1) if losing_games else None},
        "premature_resignations": sum(1 for _, ann in analyzed if ann.get("premature_resign")),
    }


def _tree_insert(root: dict[str, Any], game: ParsedGame, ann: dict | None, max_plies: int) -> None:
    node = root
    for mv in game.moves[:max_plies]:
        child = node["children"].get(mv.san)
        if child is None:
            child = {"san": mv.san, "games": 0, "wins": 0, "draws": 0, "losses": 0, "children": {}, "ply": mv.ply,
                     "fen": mv.fen_after, "uci": mv.uci, "fen_before": mv.fen_before}
            node["children"][mv.san] = child
        child["games"] += 1
        child[{"win": "wins", "draw": "draws", "loss": "losses"}[game.player_result]] += 1
        node = child


def _tree_finalize(node: dict[str, Any], min_games: int) -> dict[str, Any]:
    kids = [v for v in node["children"].values() if v["games"] >= min_games]
    kids.sort(key=lambda k: -k["games"])
    return {
        "san": node.get("san"),
        "ply": node.get("ply", 0),
        "games": node.get("games", 0),
        "wins": node.get("wins", 0),
        "draws": node.get("draws", 0),
        "losses": node.get("losses", 0),
        "score": _score(node.get("wins", 0), node.get("draws", 0), node.get("losses", 0)),
        "fen": node.get("fen"),
        "fen_before": node.get("fen_before"),
        "uci": node.get("uci"),
        "children": [_tree_finalize(k, min_games) for k in kids[:8]],
    }


def opening_name_for(g: ParsedGame, ann: dict | None) -> tuple[str, str]:
    """chess.com's label when it has one (it knows deep lines), otherwise our book's name."""
    bk = (ann or {}).get("book") or {}
    name = g.opening_name or bk.get("name") or "Unknown"
    family = g.opening_family or (bk.get("name") or "Unknown").split(":")[0].strip()
    return name, family


def openings_section(games: list[ParsedGame], analyzed_by_id: dict[str, dict], all_annotations: dict[str, dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for color in ("white", "black"):
        col_games = [g for g in games if g.player_color == color and g.rules == "chess"]
        by_name: dict[str, list[ParsedGame]] = defaultdict(list)
        by_family: dict[str, list[ParsedGame]] = defaultdict(list)
        for g in col_games:
            name, family = opening_name_for(g, all_annotations.get(g.id))
            by_name[name].append(g)
            by_family[family].append(g)

        def summarize(name: str, gs: list[ParsedGame]) -> dict[str, Any]:
            e = {"name": name, **_wdl(gs), "eco": Counter(g.eco for g in gs if g.eco).most_common(1)[0][0] if any(g.eco for g in gs) else None}
            anns = [analyzed_by_id[g.id] for g in gs if g.id in analyzed_by_id]
            e["analyzed"] = len(anns)
            e["accuracy"] = _mean(a[color]["accuracy"] for a in anns)
            e["acpl_opening"] = _mean(a[color].get("acpl_opening") for a in anns)
            evals = [pov(a["eval_after_opening"], color) for a in anns if a.get("eval_after_opening") is not None]
            e["avg_eval_after_opening"] = round(sum(evals) / len(evals)) if evals else None
            e["opening_blunders"] = sum(
                1 for a in anns for m in _player_moves(a) if m.get("phase") == "opening" and m.get("class") in ("blunder", "mistake")
            )
            e["last_played"] = max(g.end_time for g in gs)
            e["example_ids"] = [g.id for g in sorted(gs, key=lambda g: -g.end_time)[:3]]
            # who leaves the book first, and the player's typical first mistake
            left = Counter()
            first_errors: Counter = Counter()
            first_error_detail: dict[tuple[int, str], dict] = {}
            for g in gs:
                a = all_annotations.get(g.id) or {}
                bk = a.get("book") or {}
                if bk.get("deviation_ply"):
                    left[bk["deviated_by"]] += 1
                fe = a.get("first_error")
                if fe:
                    key = (fe["ply"], fe["san"])
                    first_errors[key] += 1
                    first_error_detail[key] = fe
            e["left_book_first"] = {"player": left["player"], "opponent": left["opponent"]}
            e["typical_mistakes"] = [
                {"ply": k[0], "san": k[1], "games": n, "best_san": first_error_detail[k].get("best_san"),
                 "class": first_error_detail[k].get("class"), "fen": first_error_detail[k].get("fen"),
                 "uci": first_error_detail[k].get("uci"), "best": first_error_detail[k].get("best")}
                for k, n in first_errors.most_common(3) if n >= 2 or len(gs) < 4
            ]
            return e

        names = sorted((summarize(n, gs) for n, gs in by_name.items()), key=lambda e: -e["games"])
        families = sorted((summarize(n, gs) for n, gs in by_family.items()), key=lambda e: -e["games"])
        root = {"children": {}}
        for g in col_games:
            _tree_insert(root, g, analyzed_by_id.get(g.id), max_plies=10)
        min_games = 2 if len(col_games) >= 20 else 1
        tree = _tree_finalize(root, min_games)
        tree["games"] = len(col_games)
        # the player's most common departures from the book, with how those games went
        dev: dict[tuple[int, str], list[ParsedGame]] = defaultdict(list)
        dev_book: dict[tuple[int, str], list[str]] = {}
        dev_pos: dict[tuple[int, str], dict[str, Any]] = {}
        left_first = Counter()
        for g in col_games:
            bk = (all_annotations.get(g.id) or {}).get("book") or {}
            if bk.get("deviation_ply"):
                left_first[bk["deviated_by"]] += 1
                if bk["deviated_by"] == "player":
                    key = (bk["deviation_ply"], bk["played"])
                    dev[key].append(g)
                    dev_book[key] = bk["book_moves"]
                    if key not in dev_pos:
                        mv = g.moves[bk["deviation_ply"] - 1]
                        board = chess.Board(mv.fen_before)
                        ucis = []
                        for san in bk["book_moves"][:4]:
                            try:
                                ucis.append(board.parse_san(san).uci())
                            except ValueError:
                                pass
                        dev_pos[key] = {"fen": mv.fen_before, "uci": mv.uci, "book_ucis": ucis}
        deviations = sorted(
            ({"ply": k[0], "san": k[1], "book_moves": dev_book[k][:4], **dev_pos[k], **_wdl(v)} for k, v in dev.items()),
            key=lambda d: -d["games"],
        )
        out[color] = {
            "games": len(col_games),
            "openings": names[:40],
            "families": families[:20],
            "tree": tree,
            "distinct_openings": len(by_name),
            "left_book_first": {"player": left_first["player"], "opponent": left_first["opponent"],
                                "neither": len(col_games) - left_first["player"] - left_first["opponent"]},
            "deviations": deviations[:10],
        }
    return out


def time_section(games: list[ParsedGame], analyzed: list[tuple[ParsedGame, dict]], all_annotations: dict[str, dict]) -> dict[str, Any]:
    timed = [g for g in games if g.base_seconds and g.id in all_annotations and all_annotations[g.id].get("has_clocks")]
    if not timed:
        return {"available": False}
    by_tc: dict[str, Any] = {}
    for tc in sorted({g.time_class for g in timed}):
        gs = [g for g in timed if g.time_class == tc]
        phase_time = {ph: [] for ph in PHASES}
        used_by_move10 = []
        tt_games = 0
        tt_losses = 0
        avg_left_at_end = []
        premoves = 0
        for g in gs:
            ann = all_annotations[g.id]
            me = _player_moves(ann)
            premoves += sum(1 for m in me if m.get("premove"))
            for ph in PHASES:
                spent = [m["time_spent"] for m in me if m["phase"] == ph and m["time_spent"] is not None]
                if spent:
                    phase_time[ph].append(sum(spent))
            first10 = [m["time_spent"] for m in me[:10] if m["time_spent"] is not None]
            if first10 and g.base_seconds:
                used_by_move10.append(100 * sum(first10) / g.base_seconds)
            thr = ann.get("time_trouble_threshold")
            clocks = [m["clock"] for m in me if m["clock"] is not None]
            if thr and clocks and min(clocks) < thr:
                tt_games += 1
                if g.player_result == "loss":
                    tt_losses += 1
            if clocks:
                avg_left_at_end.append(clocks[-1])
        timeouts = sum(1 for g in gs if g.player_result == "loss" and g.player_result_code == "timeout")
        won_on_time = sum(1 for g in gs if g.player_result == "win" and g.opponent_result_code == "timeout")
        by_tc[tc] = {
            "games": len(gs),
            "avg_time_by_phase": {ph: _mean(v) for ph, v in phase_time.items()},
            "pct_clock_used_by_move_10": _mean(used_by_move10),
            "time_trouble_games": tt_games,
            "time_trouble_losses": tt_losses,
            "timeouts": timeouts,
            "won_on_time": won_on_time,
            "avg_clock_left_at_end": _mean(avg_left_at_end),
            "premoves": premoves,
            "premoves_per_game": round(premoves / len(gs), 1) if gs else None,
        }

    # blunder rate in time trouble vs normal (needs engine)
    tt_moves = tt_bl = norm_moves = norm_bl = 0
    speed_buckets: dict[str, list[int]] = {"<2s": [], "2-5s": [], "5-15s": [], "15-30s": [], "30s+": []}
    for g, ann in analyzed:
        thr = ann.get("time_trouble_threshold")
        for m in _player_moves(ann):
            if "cp_loss" not in m:
                continue
            if thr and m["clock"] is not None:
                if m["clock"] < thr:
                    tt_moves += 1
                    tt_bl += m["class"] in ("blunder", "mistake")
                else:
                    norm_moves += 1
                    norm_bl += m["class"] in ("blunder", "mistake")
            ts = m["time_spent"]
            if m.get("premove"):
                continue
            if ts is not None and m["ply"] > 10 and g.base_seconds and g.base_seconds >= 180:
                key = "<2s" if ts < 2 else "2-5s" if ts < 5 else "5-15s" if ts < 15 else "15-30s" if ts < 30 else "30s+"
                speed_buckets[key].append(m["cp_loss"])
    return {
        "available": True,
        "by_time_class": by_tc,
        "error_rate": {
            "time_trouble": {"moves": tt_moves, "errors": tt_bl,
                             "rate": round(100 * tt_bl / tt_moves, 1) if tt_moves else None},
            "normal": {"moves": norm_moves, "errors": norm_bl,
                       "rate": round(100 * norm_bl / norm_moves, 1) if norm_moves else None},
        },
        "cp_loss_by_think_time": [
            {"bucket": k, "n": len(v), "avg_cp_loss": round(sum(v) / len(v), 1) if v else None}
            for k, v in speed_buckets.items()
        ],
    }


def tactics_section(analyzed: list[tuple[ParsedGame, dict]]) -> dict[str, Any]:
    if not analyzed:
        return {"available": False}
    tag_counts: Counter = Counter()
    tag_games: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    piece_hung: Counter = Counter()
    opp_blunders = 0
    opp_blunders_punished = 0
    mates_missed = 0
    for g, ann in analyzed:
        moves = ann["moves"]
        for i, m in enumerate(moves):
            if m["color"] != g.player_color:
                # did the player punish an opponent mistake?
                if m.get("win_loss", 0) >= 15 and i + 1 < len(moves) and "win_loss" in moves[i + 1]:
                    opp_blunders += 1
                    if moves[i + 1]["win_loss"] < 10:
                        opp_blunders_punished += 1
                continue
            for t in m.get("tags", []):
                if t in PHASES:
                    continue
                tag_counts[t] += 1
                tag_games[t].add(g.id)
                if len(examples[t]) < 4:
                    examples[t].append({"game_id": g.id, "ply": m["ply"], "san": m["san"], "best_san": m.get("best_san"),
                                        "win_loss": m.get("win_loss"), "opponent": g.opponent, "date": g.end_time,
                                        "fen": moves[i - 1]["fen"] if i else ann.get("start_fen"),
                                        "uci": m["uci"], "best": m.get("best"), "side": g.player_color,
                                        "class": m.get("class")})
                if t == "hung_piece":
                    # which piece got lost? look at the opponent's capture
                    nxt = moves[i + 1] if i + 1 < len(moves) else None
                    if nxt and nxt.get("capture"):
                        piece_hung[m["piece"]] += 1
            if "missed_mate" in m.get("tags", []):
                mates_missed += 1
    n_games = len(analyzed)
    return {
        "available": True,
        "tag_counts": [{"tag": t, "label": TAG_LABELS.get(t, t), "count": c, "per_game": round(c / n_games, 2),
                        "games": len(tag_games[t]), "games_pct": round(100 * len(tag_games[t]) / n_games, 1)}
                       for t, c in tag_counts.most_common()],
        "examples": dict(examples),
        "opponent_blunders": {"count": opp_blunders, "punished": opp_blunders_punished,
                              "punish_pct": round(100 * opp_blunders_punished / opp_blunders, 1) if opp_blunders else None},
        "mates_missed": mates_missed,
        "pieces_hung": dict(piece_hung.most_common()),
    }


def endgames_section(games: list[ParsedGame], analyzed: list[tuple[ParsedGame, dict]], all_annotations: dict[str, dict]) -> dict[str, Any]:
    reached = [g for g in games if g.id in all_annotations and all_annotations[g.id]["phases"]["endgame_start_ply"]]
    if not games:
        return {"available": False}
    by_type: dict[str, list[ParsedGame]] = defaultdict(list)
    for g in reached:
        by_type[all_annotations[g.id]["phases"]["endgame_type"] or "other"].append(g)
    conv = {"winning": [0, 0], "drawish": [0, 0, 0], "losing": [0, 0]}  # [games, achieved...]
    type_rows = []
    for t, gs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        row = {"type": t, **_wdl(gs)}
        wins_conv = [0, 0]
        holds = [0, 0]
        for g in gs:
            ann = all_annotations[g.id]
            if ann.get("eval_at_endgame") is None:
                continue
            e = pov(ann["eval_at_endgame"], g.player_color)
            if e >= 200:
                wins_conv[0] += 1
                wins_conv[1] += g.player_result == "win"
                conv["winning"][0] += 1
                conv["winning"][1] += g.player_result == "win"
            elif e <= -200:
                conv["losing"][0] += 1
                conv["losing"][1] += g.player_result != "loss"
            else:
                holds[0] += 1
                holds[1] += g.player_result != "loss"
                conv["drawish"][0] += 1
                conv["drawish"][1] += g.player_result != "loss"
                conv["drawish"][2] += g.player_result == "win"
        row["winning_endgames"] = wins_conv[0]
        row["converted"] = wins_conv[1]
        row["conversion_pct"] = round(100 * wins_conv[1] / wins_conv[0], 1) if wins_conv[0] else None
        row["balanced_endgames"] = holds[0]
        row["held_pct"] = round(100 * holds[1] / holds[0], 1) if holds[0] else None
        acpl = _mean(all_annotations[g.id][g.player_color].get("acpl_endgame") for g in gs if all_annotations[g.id].get("engine"))
        row["acpl"] = acpl
        type_rows.append(row)
    return {
        "available": True,
        "games_reaching_endgame": len(reached),
        "reach_pct": round(100 * len(reached) / len(games), 1),
        "results_in_endgames": _wdl(reached),
        "by_type": type_rows,
        "conversion": {
            "winning": {"games": conv["winning"][0], "won": conv["winning"][1],
                        "pct": round(100 * conv["winning"][1] / conv["winning"][0], 1) if conv["winning"][0] else None},
            "balanced": {"games": conv["drawish"][0], "not_lost": conv["drawish"][1], "won": conv["drawish"][2],
                         "hold_pct": round(100 * conv["drawish"][1] / conv["drawish"][0], 1) if conv["drawish"][0] else None},
            "losing": {"games": conv["losing"][0], "saved": conv["losing"][1],
                       "save_pct": round(100 * conv["losing"][1] / conv["losing"][0], 1) if conv["losing"][0] else None},
        },
        "acpl_endgame": _mean(all_annotations[g.id][g.player_color].get("acpl_endgame") for g in reached if all_annotations[g.id].get("engine")),
    }


def puzzle_candidates(g: ParsedGame, ann: dict[str, Any]) -> list[dict[str, Any]]:
    """Player mistakes/blunders that could become puzzles (before MultiPV verification)."""
    out = []
    moves = ann["moves"]
    for i, m in enumerate(moves):
        if m["color"] != g.player_color or m.get("class") not in ("blunder", "mistake"):
            continue
        if not m.get("best") or m.get("win_loss", 0) < 15:
            continue
        fen_before = moves[i - 1]["fen"] if i else ann.get("start_fen")
        if fen_before is None:
            continue
        tags = [t for t in m.get("tags", []) if t not in PHASES]
        theme = next((t for t in ("missed_mate", "missed_material", "hung_piece", "allowed_mate", "walked_into_fork",
                                  "bad_trade", "lost_material", "threw_away_win", "collapsed") if t in tags), "improve")
        out.append({
            "game_id": g.id,
            "ply": m["ply"],
            "fen": fen_before,
            "side": g.player_color,
            "played": m["san"],
            "played_uci": m["uci"],
            "best": m["best"],
            "best_san": m.get("best_san"),
            "pv": m.get("pv", []),
            "win_loss": m["win_loss"],
            "theme": theme,
            "theme_label": TAG_LABELS.get(theme, "Find the better move"),
            "opponent": g.opponent,
            "date": g.end_time,
            "eval_before": m.get("eval_before"),
            "accepted": [a["uci"] for a in m.get("alternatives", [])] or [m["best"]],
            "accepted_san": [a["san"] for a in m.get("alternatives", [])] or [m.get("best_san") or m["best"]],
            "only_move": bool(m.get("only_move")),
            "verified": "alternatives" in m,
            "played_is_fine": bool(m.get("played_is_fine")),
        })
    return out


def puzzles_section(analyzed: list[tuple[ParsedGame, dict]], limit: int = 40) -> list[dict[str, Any]]:
    """Positions from the player's own games where they missed a clearly better move.

    Verified positions where MultiPV showed the played move was actually fine are dropped, and
    positions with more than three acceptable answers are considered too vague to be puzzles."""
    cands = []
    for g, ann in analyzed:
        for c in puzzle_candidates(g, ann):
            if c["played_is_fine"] or len(c["accepted"]) > 3:
                continue
            cands.append(c)
    priority = {"missed_mate": 0, "missed_material": 1, "hung_piece": 2, "allowed_mate": 3, "walked_into_fork": 4}
    cands.sort(key=lambda c: (priority.get(c["theme"], 5), not c["only_move"], -c["win_loss"]))
    # keep at most 2 per game so the set is varied
    per_game: Counter = Counter()
    out = []
    for c in cands:
        if per_game[c["game_id"]] >= 2:
            continue
        per_game[c["game_id"]] += 1
        out.append(c)
        if len(out) >= limit:
            break
    return out


def games_list(games: list[ParsedGame], all_annotations: dict[str, dict]) -> list[dict[str, Any]]:
    rows = []
    for g in sorted(games, key=lambda g: -g.end_time):
        ann = all_annotations.get(g.id)
        me = ann[g.player_color] if ann and ann.get("engine") else None
        rows.append({
            "id": g.id,
            "url": g.url,
            "date": g.end_time,
            "start": g.start_time,
            "time_class": g.time_class,
            "time_control": g.time_control,
            "rated": g.rated,
            "rules": g.rules,
            "color": g.player_color,
            "opponent": g.opponent,
            "opponent_rating": g.opponent_rating,
            "player_rating": g.player_rating,
            "result": g.player_result,
            "termination": g.termination,
            "opening": g.opening_name,
            "eco": g.eco,
            "moves": (len(g.moves) + 1) // 2,
            "analyzed": bool(me),
            "accuracy": me["accuracy"] if me else None,
            "acpl": me["acpl"] if me else None,
            "blunders": me["classes"]["blunder"] if me else None,
            "mistakes": me["classes"]["mistake"] if me else None,
            "chesscom_accuracy": g.chesscom_accuracy,
        })
    return rows


def build_report(
    username: str,
    profile: dict[str, Any],
    stats: dict[str, Any],
    games: list[ParsedGame],
    annotations: dict[str, dict[str, Any]],
    options: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    games = [g for g in games if g.moves]
    analyzed = [(g, annotations[g.id]) for g in games if g.id in annotations and annotations[g.id].get("engine")]
    analyzed.sort(key=lambda t: -t[0].end_time)
    analyzed_by_id = {g.id: a for g, a in analyzed}
    report: dict[str, Any] = {
        "player": {
            "username": username,
            "name": profile.get("name"),
            "title": profile.get("title"),
            "avatar": profile.get("avatar"),
            "url": profile.get("url"),
            "country": (profile.get("country") or "").rstrip("/").split("/")[-1] or None,
            "joined": profile.get("joined"),
            "last_online": profile.get("last_online"),
        },
        "generated_at": time.time(),
        "options": options,
        "overview": overview_section(games, analyzed, stats),
        "ratings": ratings_section(games),
        "results": results_section(games),
        "accuracy": accuracy_section(analyzed),
        "openings": openings_section(games, analyzed_by_id, annotations),
        "time": time_section(games, analyzed, annotations),
        "tactics": tactics_section(analyzed),
        "endgames": endgames_section(games, analyzed, annotations),
        "puzzles": puzzles_section(analyzed),
        "games": games_list(games, annotations),
    }
    report["overview"]["unknown_result_codes"] = sum(1 for g in games if not g.result_known)
    report["insights"] = build_insights(report)
    report["training_plan"] = build_training_plan(report["insights"])
    report["previous"] = previous
    return report


def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    """The few numbers worth keeping per run, so the next report can show what changed."""
    ov, acc = report["overview"], report["accuracy"]
    return {
        "generated_at": report["generated_at"],
        "games_total": ov["games_total"],
        "games_analyzed": ov["games_analyzed"],
        "score": ov["all"]["score"],
        "ratings": {tc: e["rating_now"] for tc, e in ov["by_time_class"].items()},
        "accuracy": acc["overall"]["accuracy"] if acc.get("available") else None,
        "acpl": acc["overall"]["acpl"] if acc.get("available") else None,
        "blunders_per_game": acc["overall"]["blunders_per_game"] if acc.get("available") else None,
        "conversion_pct": acc["winning_positions"]["conversion_pct"] if acc.get("available") else None,
        "top_insights": [i["id"] for i in report["insights"][:5]],
    }

"""Turn the aggregated report into ranked, plain-language findings and a training plan.

Every rule is deliberately conservative about sample sizes: an insight needs enough games
or moves behind it before it is allowed to say anything.
"""
from __future__ import annotations

from typing import Any

MIN_GAMES = 8


def _ins(id_: str, category: str, severity: str, title: str, detail: str, recommendation: str,
         impact: float, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": id_, "category": category, "severity": severity, "title": title, "detail": detail,
            "recommendation": recommendation, "impact": round(impact, 2), "evidence": evidence or {}}


def _example(r: dict[str, Any], *tags: str) -> dict[str, Any] | None:
    ex = (r.get("tactics") or {}).get("examples") or {}
    for t in tags:
        if ex.get(t):
            return ex[t][0]
    return None


def build_insights(r: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    _attach_examples_after = True
    ov, res, acc, op, tm, tac, eg = (r["overview"], r["results"], r["accuracy"], r["openings"], r["time"],
                                     r["tactics"], r["endgames"])
    total = ov["games_total"]
    if total == 0:
        return out
    base_score = ov["all"]["score"] or 50.0

    # ---- results & time class ---------------------------------------------------------------
    for tc, e in ov["by_time_class"].items():
        if e["games"] >= MIN_GAMES and e["score"] is not None:
            losses = res["termination_losses"]
            # timeouts are counted per time class inside the time section
            t = (tm.get("by_time_class") or {}).get(tc)
            if t and e["losses"] and t["timeouts"] / e["losses"] >= 0.2 and t["timeouts"] >= 3:
                pct = round(100 * t["timeouts"] / e["losses"])
                out.append(_ins(
                    f"timeouts_{tc}", "time", "high",
                    f"{pct}% of your {tc} losses are on time",
                    f"You lost {t['timeouts']} of {e['losses']} {tc} games by running out of clock. "
                    f"Those are points thrown away in positions that were often still playable.",
                    "Play a time control with increment for a few weeks, and adopt a simple rule: when you are "
                    "under a quarter of your starting time, play the first reasonable move you see. Practise "
                    "moving with the mouse quickly (premoves in forced sequences) so the clock is a tool, not an enemy.",
                    impact=6 + pct / 10, evidence={"timeouts": t["timeouts"], "losses": e["losses"], "time_class": tc},
                ))
    # color imbalance
    w, b = ov["by_color"]["white"], ov["by_color"]["black"]
    if w["games"] >= MIN_GAMES and b["games"] >= MIN_GAMES and w["score"] is not None and b["score"] is not None:
        gap = w["score"] - b["score"]
        if abs(gap) >= 12:
            worse = "Black" if gap > 0 else "White"
            out.append(_ins(
                "color_gap", "openings", "medium",
                f"You score {abs(gap):.0f} points worse with {worse}",
                f"White: {w['score']}% over {w['games']} games. Black: {b['score']}% over {b['games']} games. "
                f"A gap this large usually points at the opening repertoire rather than at general strength.",
                f"Review your most played openings as {worse} below. Pick one solid system against 1.e4 and one "
                f"against 1.d4 (or one for each of your first moves as White) and learn the typical plans, not just the moves.",
                impact=4 + abs(gap) / 5, evidence={"white": w, "black": b},
            ))

    # ---- openings -------------------------------------------------------------------------
    for color in ("white", "black"):
        rows = [o for o in op[color]["openings"] if o["games"] >= 6 and o["score"] is not None]
        for o in rows:
            if o["score"] <= base_score - 12:
                out.append(_ins(
                    f"opening_{color}_{o['name']}", "openings", "high" if o["games"] >= 12 else "medium",
                    f"{o['name']} is costing you points as {color}",
                    f"{o['score']}% over {o['games']} games (your average is {base_score}%)."
                    + (f" After 10 moves the engine already has you at {o['avg_eval_after_opening'] / 100:+.1f} on average."
                       if o.get("avg_eval_after_opening") is not None else ""),
                    "Either study this line properly (the first 10 moves and the two or three typical plans) or "
                    "replace it with something you understand. Play through your losses in it and find the move "
                    "where things went wrong; it is usually the same mistake each time.",
                    impact=3 + (base_score - o["score"]) / 6 + o["games"] / 10,
                    evidence={"opening": o, "color": color},
                ))
        best = [o for o in rows if o["score"] >= base_score + 12 and o["games"] >= 8]
        for o in best[:1]:
            out.append(_ins(
                f"opening_good_{color}_{o['name']}", "openings", "positive",
                f"{o['name']} is your strongest weapon as {color}",
                f"{o['score']}% over {o['games']} games. Keep it, and deepen it.",
                "Play it more often and study a few master games in it to add ideas beyond move 10.",
                impact=1.5, evidence={"opening": o, "color": color},
            ))
        if op[color]["games"] >= 30 and op[color]["distinct_openings"] > op[color]["games"] * 0.6:
            out.append(_ins(
                f"opening_scatter_{color}", "openings", "medium",
                f"Your {color} repertoire is scattered",
                f"{op[color]['distinct_openings']} different openings across {op[color]['games']} games. You rarely get "
                f"the same positions, so you never get to learn them.",
                "Narrow the repertoire: one reply to each of the opponent's main first moves. Repetition is how "
                "opening knowledge turns into pattern recognition.",
                impact=3, evidence={"distinct": op[color]["distinct_openings"], "games": op[color]["games"]},
            ))

    # ---- accuracy & phases ------------------------------------------------------------------
    if acc.get("available"):
        ph = acc["by_phase"]
        rates = {p: v["blunder_rate_per_100"] for p, v in ph.items() if v["moves"] >= 60 and v["blunder_rate_per_100"] is not None}
        if rates:
            worst = max(rates, key=rates.get)
            avg = sum(rates.values()) / len(rates)
            if rates[worst] >= avg * 1.3 and rates[worst] >= 1.5:
                phase_tips = {
                    "opening": "Learn the ideas behind your openings (piece placement, pawn breaks) and do a quick "
                               "'what does this move threaten' check on every opponent move before move 12.",
                    "middlegame": "Before each move ask three questions: what did the last move threaten, what are my "
                                  "loose pieces, what is my worst-placed piece. Solve tactics puzzles daily; most "
                                  "middlegame blunders are missed one-move tactics.",
                    "endgame": "Study the basic technical endgames (king and pawn, Lucena and Philidor rook endings, "
                               "opposition). In your games, slow down when the queens come off; endgame moves are "
                               "often the cheapest to calculate precisely.",
                }
                out.append(_ins(
                    f"phase_{worst}", "accuracy", "high",
                    f"The {worst} is where your games fall apart",
                    f"You blunder {rates[worst]:.1f} times per 100 {worst} moves"
                    + (f", against {avg:.1f} on average across phases" if len(rates) > 1 else "")
                    + f". Average centipawn loss in the {worst}: {ph[worst]['acpl']}.",
                    phase_tips[worst], impact=5 + rates[worst], evidence={"rates": rates, "phase": ph[worst]},
                ))
        wp = acc["winning_positions"]
        if wp["games"] >= MIN_GAMES and wp["conversion_pct"] is not None and wp["conversion_pct"] < 75:
            out.append(_ins(
                "conversion", "accuracy", "high",
                f"You convert only {wp['conversion_pct']:.0f}% of clearly winning positions",
                f"In {wp['games']} analysed games you reached a +3 advantage; {wp['not_won']} of them were not won. "
                f"Winning won games is the single biggest rating lever at most levels.",
                "When you are clearly ahead: trade pieces (not pawns), remove counterplay before grabbing more "
                "material, and keep your king safe. Play the 'technique' phase slower, not faster.",
                impact=5 + (75 - wp["conversion_pct"]) / 5, evidence=wp,
            ))
        lp = acc["losing_positions"]
        if lp["games"] >= MIN_GAMES and lp["save_pct"] is not None and lp["save_pct"] >= 30:
            out.append(_ins(
                "swindles", "accuracy", "positive",
                f"You save {lp['save_pct']:.0f}% of lost positions",
                f"{lp['saved']} of {lp['games']} games where the engine had you at -3 or worse ended in a draw or win. "
                "You fight well, or your opponents collapse under pressure.",
                "Keep fighting in bad positions, but note how often the reverse happens to you (see conversion).",
                impact=1, evidence=lp,
            ))
        if acc["premature_resignations"] >= 3:
            out.append(_ins(
                "premature_resign", "accuracy", "medium",
                f"You resigned {acc['premature_resignations']} games that were still holdable",
                "The engine had the position within a pawn and a half when you resigned.",
                "Never resign while there is still a realistic chance the opponent goes wrong. At club level, most "
                "'lost' positions are not lost yet.",
                impact=3 + acc["premature_resignations"] / 3, evidence={"count": acc["premature_resignations"]},
            ))
        bc = acc["by_color"]
        if all(bc[c]["games"] >= MIN_GAMES and bc[c]["acpl"] is not None for c in ("white", "black")):
            gap = bc["black"]["acpl"] - bc["white"]["acpl"]
            if abs(gap) >= 15:
                worse = "Black" if gap > 0 else "White"
                out.append(_ins(
                    "acpl_color", "accuracy", "low",
                    f"You play noticeably less accurately as {worse}",
                    f"Average centipawn loss: {bc['white']['acpl']} as White, {bc['black']['acpl']} as Black.",
                    f"Look at your {worse} openings: inaccurate play usually starts with unfamiliar positions.",
                    impact=2 + abs(gap) / 15, evidence=bc,
                ))
        by_res = acc["by_result"]
        if by_res["loss"]["games"] >= MIN_GAMES and by_res["loss"]["blunders_per_game"] is not None:
            bpg = by_res["loss"]["blunders_per_game"]
            if bpg >= 1.5:
                out.append(_ins(
                    "blunders_in_losses", "tactics", "high",
                    f"Your losses contain {bpg:.1f} blunders each on average",
                    "You are not being outplayed slowly; games are decided by single moves that give away 20% or "
                    "more win probability. That is the most fixable kind of loss.",
                    "Adopt a blunder check: before you move, look at every capture and check the opponent has after "
                    "your intended move. 15 minutes of tactics puzzles a day builds the reflex.",
                    impact=6 + bpg, evidence=by_res["loss"],
                ))

    # ---- time management --------------------------------------------------------------------
    if tm.get("available"):
        er = tm["error_rate"]
        if er["time_trouble"]["moves"] >= 40 and er["normal"]["moves"] >= 100 and er["normal"]["rate"]:
            ratio = (er["time_trouble"]["rate"] or 0) / er["normal"]["rate"]
            if ratio >= 1.6:
                out.append(_ins(
                    "time_trouble_errors", "time", "high",
                    f"You blunder {ratio:.1f}x more often in time trouble",
                    f"Error rate (mistakes + blunders): {er['time_trouble']['rate']}% of moves in time trouble vs "
                    f"{er['normal']['rate']}% otherwise, across {er['time_trouble']['moves']} time-trouble moves.",
                    "The fix is earlier in the game: spend less time on the first 10 moves and on obvious recaptures, "
                    "and bank the saved time for the critical middlegame decisions. Increment time controls help.",
                    impact=5 + ratio, evidence=er,
                ))
        for tc, t in tm["by_time_class"].items():
            if t["games"] >= MIN_GAMES and t["pct_clock_used_by_move_10"] and t["pct_clock_used_by_move_10"] >= 25:
                out.append(_ins(
                    f"slow_opening_{tc}", "time", "medium",
                    f"You spend {t['pct_clock_used_by_move_10']:.0f}% of your {tc} clock on the first 10 moves",
                    "The opening is the phase where preparation should make you fast. Spending a quarter of the clock "
                    "there means you are working things out over the board every game.",
                    "Fix a repertoire for your main lines and drill the first 8-10 moves until they are automatic.",
                    impact=3 + t["pct_clock_used_by_move_10"] / 10, evidence={"time_class": tc, **t},
                ))
            if t["games"] >= MIN_GAMES and t.get("avg_clock_left_at_end") and t["games"] and t["timeouts"] == 0:
                pass
        think = {b["bucket"]: b for b in tm["cp_loss_by_think_time"]}
        fast, slow = think.get("<2s"), think.get("5-15s")
        if fast and slow and fast["n"] >= 80 and slow["n"] >= 80 and fast["avg_cp_loss"] and slow["avg_cp_loss"]:
            if fast["avg_cp_loss"] >= slow["avg_cp_loss"] * 1.6:
                out.append(_ins(
                    "fast_moves", "time", "medium",
                    "Your instant moves are your worst moves",
                    f"Moves played in under 2 seconds lose {fast['avg_cp_loss']} centipawns on average; moves with "
                    f"5-15 seconds of thought lose {slow['avg_cp_loss']}. ({fast['n']} fast moves measured.)",
                    "Outside of forced recaptures, never move instantly after move 10. A two-second scan for the "
                    "opponent's threats costs nothing and catches most one-move blunders.",
                    impact=4 + fast["avg_cp_loss"] / 40, evidence={"fast": fast, "slow": slow},
                ))

    # ---- tactics ----------------------------------------------------------------------------
    if tac.get("available"):
        tags = {t["tag"]: t for t in tac["tag_counts"]}
        n_an = ov["games_analyzed"]
        if n_an >= MIN_GAMES:
            hung = tags.get("hung_piece", {}).get("count", 0) + tags.get("lost_material", {}).get("count", 0)
            hung_games = (tags.get("hung_piece", {}).get("games", 0) + tags.get("lost_material", {}).get("games", 0))
            hung_games = min(hung_games, n_an)
            if hung_games / n_an >= 0.3:
                pieces = ", ".join(f"{k}: {v}" for k, v in list(tac["pieces_hung"].items())[:3])
                out.append(_ins(
                    "hanging_pieces", "tactics", "high",
                    f"You leave a piece hanging in {round(100 * hung_games / n_an)}% of games",
                    f"{hung} moves across {n_an} analysed games simply lost material to a capture or a short tactic."
                    + (f" Pieces most often lost: {pieces}." if pieces else ""),
                    "Before every move, list your undefended pieces. Then check: after my move, can anything be taken "
                    "for free? This habit alone is worth 100+ rating points at club level.",
                    impact=6 + hung / n_an * 3, evidence={"count": hung, "games": n_an, "pieces": tac["pieces_hung"]},
                ))
            mm = tags.get("missed_material", {}).get("count", 0) + tac["mates_missed"]
            if mm / n_an >= 0.3:
                out.append(_ins(
                    "missed_tactics", "tactics", "medium",
                    f"You missed a winning tactic in roughly {round(100 * min(1, mm / n_an))}% of games",
                    f"{tags.get('missed_material', {}).get('count', 0)} missed material wins and {tac['mates_missed']} "
                    f"missed forced mates.",
                    "When the opponent's last move leaves a piece undefended or a king exposed, stop and calculate. "
                    "Solve puzzles from your own games (see the Training tab) to see the patterns you personally miss.",
                    impact=4 + mm / n_an * 3, evidence={"missed_material": tags.get("missed_material"), "mates": tac["mates_missed"]},
                ))
            ob = tac["opponent_blunders"]
            if ob["count"] >= 15 and ob["punish_pct"] is not None and ob["punish_pct"] < 60:
                out.append(_ins(
                    "punish", "tactics", "medium",
                    f"You punish only {ob['punish_pct']:.0f}% of your opponents' blunders",
                    f"Opponents handed you a big chance {ob['count']} times; you converted it into an advantage in "
                    f"{ob['punished']} of them.",
                    "Every time the opponent makes a move that looks odd, ask 'what did that move stop defending?'. "
                    "Their mistakes are your fastest wins.",
                    impact=4 + (60 - ob["punish_pct"]) / 10, evidence=ob,
                ))
            fk = tags.get("walked_into_fork", {}).get("count", 0)
            if fk / n_an >= 0.15:
                out.append(_ins(
                    "forks", "tactics", "medium",
                    f"Knight forks and double attacks catch you {fk} times in {n_an} games",
                    "Several of your material losses came from one piece attacking two of yours at once.",
                    "Practise fork-themed puzzles; in games, watch for knights two squares away from your king and "
                    "queen, and avoid placing your queen and king on the same colour complex near an enemy knight.",
                    impact=3 + fk / n_an * 5, evidence=tags.get("walked_into_fork"),
                ))

    # ---- endgames ---------------------------------------------------------------------------
    if eg.get("available"):
        cv = eg["conversion"]["winning"]
        if cv["games"] >= 6 and cv["pct"] is not None and cv["pct"] < 70:
            worst_type = None
            for row in eg["by_type"]:
                if row["winning_endgames"] >= 3 and row["conversion_pct"] is not None and row["conversion_pct"] < 70:
                    worst_type = row
                    break
            out.append(_ins(
                "endgame_conversion", "endgames", "high",
                f"You convert only {cv['pct']:.0f}% of winning endgames",
                f"You entered {cv['games']} endgames at least two pawns up (engine +2) and won {cv['won']}."
                + (f" {worst_type['type'].capitalize()} endgames are the weakest spot ({worst_type['conversion_pct']:.0f}%)."
                   if worst_type else ""),
                "Learn the technique for the endgame type you reach most: activate the king, create a passed pawn, "
                "and use the 'principle of two weaknesses'. Practise winning positions against the engine.",
                impact=5 + (70 - cv["pct"]) / 6, evidence=eg["conversion"],
            ))
        bal = eg["conversion"]["balanced"]
        if bal["games"] >= 8 and bal["hold_pct"] is not None and bal["hold_pct"] < 60:
            out.append(_ins(
                "endgame_holds", "endgames", "medium",
                f"You lose {100 - bal['hold_pct']:.0f}% of balanced endgames",
                f"Of {bal['games']} endgames that started level, you lost {bal['games'] - bal['not_lost']}.",
                "Equal endgames are lost by passivity and by the clock. Keep the king active, keep the rooks active, "
                "and do not rush: these positions reward the more patient player.",
                impact=4 + (60 - bal["hold_pct"]) / 8, evidence=bal,
            ))

    # ---- rhythm: time of day, tilt, sessions -----------------------------------------------
    hours = [h for h in res["by_hour"] if h["games"] >= 12 and h["score"] is not None]
    if hours and total >= 60:
        worst = min(hours, key=lambda h: h["score"])
        if worst["score"] <= base_score - 12:
            out.append(_ins(
                "hour", "habits", "medium",
                f"Games ending around {worst['hour']:02d}:00 UTC score {worst['score']:.0f}%",
                f"Your overall score is {base_score}%. {worst['games']} games in that hour scored {worst['score']}%.",
                "Late or tired play costs rating. Play rated games when you are fresh and keep late-night chess unrated.",
                impact=2.5 + (base_score - worst["score"]) / 8, evidence=worst,
            ))
    tilt = res["tilt"]
    if tilt["after_loss"]["games"] >= 15 and tilt["baseline"]["games"] >= 15:
        drop = (tilt["baseline"]["score"] or 0) - (tilt["after_loss"]["score"] or 0)
        if drop >= 10:
            out.append(_ins(
                "tilt", "habits", "high",
                f"You score {drop:.0f} points lower right after a loss",
                f"Games started within 20 minutes of a loss: {tilt['after_loss']['score']}% "
                f"({tilt['after_loss']['games']} games). Otherwise: {tilt['baseline']['score']}%."
                + (f" Immediate rematches after a loss: {tilt['rematch_after_loss']['score']}% over "
                   f"{tilt['rematch_after_loss']['games']} games." if tilt["rematch_after_loss"]["games"] >= 5 else ""),
                "Make a rule: after a loss, no new game for 10 minutes. Look at the loss once, find the one move to "
                "remember, then decide whether to play on. Tilt is the most expensive habit in online chess.",
                impact=5 + drop / 4, evidence=tilt,
            ))
    sc = [s for s in res["session_curve"] if s["games"] >= 12 and s["score"] is not None]
    if len(sc) >= 4:
        first = sc[0]["score"]
        late = [s for s in sc if isinstance(s["game_no"], str) or s["game_no"] >= 5]
        if late:
            late_score = sum(s["score"] * s["games"] for s in late) / sum(s["games"] for s in late)
            if first - late_score >= 12:
                out.append(_ins(
                    "long_sessions", "habits", "medium",
                    f"Your results fall {first - late_score:.0f} points deep into a session",
                    f"First game of a session: {first:.0f}%. Fifth game onwards: {late_score:.0f}%.",
                    "Cap sessions at 3-4 games, or take a break after each pair. Quality beats volume for improvement.",
                    impact=3 + (first - late_score) / 8, evidence={"session_curve": sc},
                ))
    vs = {v["bucket"]: v for v in res["vs_rating"]}
    lower = [vs[k] for k in ("200+ lower", "100-200 lower") if vs[k]["games"] >= 10 and vs[k]["score"] is not None]
    if lower:
        w = sum(v["score"] * v["games"] for v in lower) / sum(v["games"] for v in lower)
        if w < 62:
            out.append(_ins(
                "vs_lower", "habits", "medium",
                f"You score only {w:.0f}% against players rated 100+ below you",
                "Against weaker opponents the expected score is above 65%. Dropping points here usually means "
                "over-pressing, playing too fast, or underestimating them.",
                "Against lower-rated players play your normal, solid chess. Let them make the mistakes; they will.",
                impact=3 + (62 - w) / 5, evidence=lower,
            ))
    # a board to look at, wherever one exists
    for ins in out:
        ex = None
        cat, iid = ins["category"], ins["id"]
        if iid == "hanging_pieces":
            ex = _example(r, "hung_piece", "lost_material")
        elif iid == "missed_tactics":
            ex = _example(r, "missed_mate", "missed_material")
        elif iid == "forks":
            ex = _example(r, "walked_into_fork")
        elif iid == "punish":
            ex = _example(r, "missed_opponent_blunder")
        elif iid in ("conversion", "endgame_conversion"):
            ex = _example(r, "threw_away_win")
        elif iid == "time_trouble_errors":
            ex = _example(r, "time_trouble")
        elif iid == "fast_moves":
            ex = _example(r, "rushed")
        elif iid == "blunders_in_losses":
            ex = _example(r, "hung_piece", "allowed_mate", "collapsed")
        elif iid.startswith("phase_"):
            ex = ((r.get("accuracy") or {}).get("phase_examples") or {}).get(iid[len("phase_"):])
        elif iid.startswith("opening_") and ins["evidence"].get("opening"):
            tm = ins["evidence"]["opening"].get("typical_mistakes") or []
            if tm and tm[0].get("fen"):
                m = tm[0]
                ex = {"fen": m["fen"], "uci": m["uci"], "best": m["best"], "san": m["san"], "best_san": m["best_san"],
                      "side": ins["evidence"].get("color"), "ply": m["ply"], "game_id": None,
                      "caption": f"Your usual first slip in this line, seen in {m['games']} game(s)"}
        if ex:
            ins["example"] = {k: ex.get(k) for k in ("fen", "uci", "best", "san", "best_san", "side", "ply", "game_id",
                                                     "opponent", "date", "win_loss", "caption")}
    out.sort(key=lambda i: (-(i["severity"] != "positive"), -i["impact"]))
    return out


DRILLS = {
    "time": ["Play 10 games at a slower control with increment (e.g. 5+3 or 10+5) and aim to finish with 20% of "
             "your clock left.", "Set a per-move budget: opening 5s, middlegame up to 30s, endgame 10s."],
    "openings": ["Pick one line per colour and write down the first 10 moves plus two typical plans.",
                 "Replay your last five losses in the weak opening and mark the first move the engine dislikes."],
    "accuracy": ["Analyse one of your own games per day for 15 minutes; write down the single lesson.",
                 "Play 'convert the win' practice positions against the engine from your own +3 games."],
    "tactics": ["Solve 15 tactics puzzles a day, prioritising the themes flagged here.",
                "Work through the puzzles built from your own games in the Training tab."],
    "endgames": ["Study king-and-pawn opposition, Lucena and Philidor until you can play them from memory.",
                 "Play out your drawn and winning endgames from this report against the engine."],
    "habits": ["Rule: no rematch after a loss. Ten-minute break, then decide.",
               "Keep a session log: date, games, mood, result. Patterns become obvious within a month."],
}


def build_training_plan(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan = []
    seen_categories: set[str] = set()
    for ins in insights:
        if ins["severity"] == "positive" or ins["category"] in seen_categories:
            continue
        seen_categories.add(ins["category"])
        plan.append({
            "focus": ins["title"],
            "category": ins["category"],
            "why": ins["detail"],
            "how": ins["recommendation"],
            "drills": DRILLS.get(ins["category"], []),
            "insight_id": ins["id"],
        })
        if len(plan) >= 4:
            break
    return plan

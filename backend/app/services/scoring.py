"""Hybrid scoring and leaderboard computation.

Final ranking = stageBonus + matchPoints, so tournament achievement ALWAYS
outranks raw match performance (a champion can never be overtaken by a team
that finished lower). See app.services.tournament for the single source of
truth on stage order, bonuses and labels.

matchPoints (cumulative across finished matches):
    Group win  +3   ·  Group draw +1  ·  Group loss 0
    Knockout win (90/120 mins OR on penalties)  +3  ·  Knockout loss 0

Penalty shoot-out wins count as wins via the feed's `detail.winner`.
"""
import json

from app.models import Sweepstake
from app.schemas import LeaderboardRow
from app.services.tournament import stage_bonus, finish_rank

WIN, DRAW, LOSS = 3, 1, 0


def _result_points_for_team(team_name: str, fixtures) -> tuple[int, int, int, int, int]:
    """Return (points, wins, draws, losses, knockout_wins) across finished games."""
    pts = w = d = l = ko_wins = 0
    for fx in fixtures:
        if fx.status != "FINISHED":
            continue
        if team_name not in (fx.home_team, fx.away_team):
            continue
        hs, as_ = fx.home_score, fx.away_score
        if hs is None or as_ is None:
            continue
        # Determine the winner, honouring penalty shoot-outs via detail.winner.
        winner = None  # "HOME" | "AWAY" | None(=draw)
        if hs > as_:
            winner = "HOME"
        elif as_ > hs:
            winner = "AWAY"
        else:
            code = None
            if fx.detail:
                try:
                    code = (json.loads(fx.detail) or {}).get("winner")
                except Exception:
                    code = None
            if code == "HOME_TEAM":
                winner = "HOME"
            elif code == "AWAY_TEAM":
                winner = "AWAY"
            # else genuine draw

        is_home = team_name == fx.home_team
        is_knockout = (fx.stage or "Group") != "Group"
        if winner is None:
            pts += DRAW; d += 1
        elif (winner == "HOME") == is_home:
            pts += WIN; w += 1
            if is_knockout:
                ko_wins += 1
        else:
            pts += LOSS; l += 1
    return pts, w, d, l, ko_wins


def compute_leaderboard(sweepstake: Sweepstake, fixtures=None) -> list[LeaderboardRow]:
    """Build a ranked leaderboard using the hybrid (stageBonus + matchPoints) model.

    `sweepstake` must be loaded with participants -> user, allocation -> team,
    and prize_tiers. `fixtures` is the sweepstake's fixtures; if not supplied,
    falls back to sweepstake.fixtures if available, else no results (everyone 0).
    """
    if fixtures is None:
        fixtures = getattr(sweepstake, "fixtures", None) or []

    rows: list[dict] = []
    for part in sweepstake.participants:
        alloc = part.allocation
        if not alloc or not alloc.team:
            continue
        team = alloc.team
        match_pts, w, d, l, ko_wins = _result_points_for_team(team.name, fixtures)
        bonus = stage_bonus(team.stage)
        final_score = bonus + match_pts
        rows.append({
            "participant_id": part.id,
            "participant_name": part.user.username,
            "avatar_color": part.user.avatar_color,
            "team_name": team.name,
            "flag_emoji": team.flag_emoji,
            "stage": team.stage,
            # `points` is the headline final score (bonus + match points) the UI
            # ranks on; `match_points` is the secondary breakdown.
            "points": final_score,
            "match_points": match_pts,
            "stage_bonus": bonus,
            "wins": w, "draws": d, "losses": l,
            "ko_wins": ko_wins,
            "eliminated": team.eliminated,
        })

    # Primary sort: finalLeaderboardScore. Tie-breakers, in order:
    #   1) furthest stage reached  2) match points  3) knockout wins
    #   4) goal-agnostic wins (best app-safe stat we have)  5) name (stable)
    rows.sort(key=lambda r: (
        -r["points"],
        -finish_rank(r["stage"]),
        -r["match_points"],
        -r["ko_wins"],
        -r["wins"],
        r["participant_name"].lower(),
    ))

    pool = sweepstake.prize_pool
    tiers = {t.rank: t.percentage for t in sweepstake.prize_tiers}

    result: list[LeaderboardRow] = []
    for i, r in enumerate(rows, start=1):
        payout = pool * tiers.get(i, 0) / 100
        # Drop helper-only keys not in the schema before constructing the row.
        r.pop("match_points", None); r.pop("stage_bonus", None); r.pop("ko_wins", None)
        result.append(LeaderboardRow(rank=i, potential_payout=round(payout, 2), **r))
    return result

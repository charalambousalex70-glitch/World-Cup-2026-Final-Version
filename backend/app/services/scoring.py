"""Results-based scoring and leaderboard computation.

Every participant starts at 0. Their assigned team earns, cumulatively, across
every finished match:

    Win   +3
    Draw  +1
    Loss  +0

In knockout ties decided by penalties, the feed's `winner` field already
reflects who advanced, so a shoot-out win counts as a +3 win (and the other
side as a 0 loss, not a +1 draw).
"""
import json

from app.models import Sweepstake
from app.schemas import LeaderboardRow

WIN, DRAW, LOSS = 3, 1, 0


def _result_points_for_team(team_name: str, fixtures) -> tuple[int, int, int, int]:
    """Return (points, wins, draws, losses) for a team across finished games."""
    pts = w = d = l = 0
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
        if winner is None:
            pts += DRAW; d += 1
        elif (winner == "HOME") == is_home:
            pts += WIN; w += 1
        else:
            pts += LOSS; l += 1
    return pts, w, d, l


def compute_leaderboard(sweepstake: Sweepstake, fixtures=None) -> list[LeaderboardRow]:
    """Build a ranked leaderboard from match results.

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
        pts, w, d, l = _result_points_for_team(team.name, fixtures)
        rows.append({
            "participant_id": part.id,
            "participant_name": part.user.username,
            "avatar_color": part.user.avatar_color,
            "team_name": team.name,
            "flag_emoji": team.flag_emoji,
            "stage": team.stage,
            "points": pts,
            "wins": w, "draws": d, "losses": l,
            "eliminated": team.eliminated,
        })

    # Highest points first; tie-break by wins, then name.
    rows.sort(key=lambda r: (-r["points"], -r["wins"], r["participant_name"].lower()))

    pool = sweepstake.prize_pool
    tiers = {t.rank: t.percentage for t in sweepstake.prize_tiers}

    result: list[LeaderboardRow] = []
    for i, r in enumerate(rows, start=1):
        payout = pool * tiers.get(i, 0) / 100
        result.append(LeaderboardRow(rank=i, potential_payout=round(payout, 2), **r))
    return result

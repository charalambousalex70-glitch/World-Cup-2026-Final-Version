"""Scoring rules and leaderboard computation.

Knockout stages award points by the furthest round reached. During the GROUP
stage, every surviving team scores a base of 10, plus a small bonus for their
current position in their group so the table reflects how each team is doing:

    1st in group  +6
    2nd in group  +4
    3rd in group   +2   (only the best 8 third-placed teams advance)
    4th in group   +0

Once a team reaches the knockouts, stage points take over and are always higher
than any group total, so progression always increases your score.
"""
import json

from app.models import Sweepstake
from app.schemas import LeaderboardRow

STAGE_POINTS: dict[str, int] = {
    "Group": 10, "R16": 25, "QF": 45, "SF": 70, "Final": 90, "Winner": 120, "Out": 0,
}
GROUP_POSITION_BONUS = {1: 6, 2: 4, 3: 2, 4: 0}


def points_for_stage(stage: str) -> int:
    return STAGE_POINTS.get(stage, 0)


def _position_lookup(sweepstake: Sweepstake) -> dict[str, int]:
    out: dict[str, int] = {}
    raw = getattr(sweepstake, "standings", None)
    if not raw:
        return out
    try:
        groups = json.loads(raw)
    except Exception:
        return out
    for table in (groups or {}).values():
        for row in table:
            name, pos = row.get("team"), row.get("position")
            if name and pos:
                out[name] = pos
    return out


def points_for(team, positions: dict[str, int]) -> int:
    if team.stage == "Out":
        return 0
    if team.stage == "Group":
        pos = positions.get(team.name)
        return STAGE_POINTS["Group"] + (GROUP_POSITION_BONUS.get(pos, 0) if pos else 0)
    return points_for_stage(team.stage)


def compute_leaderboard(sweepstake: Sweepstake) -> list[LeaderboardRow]:
    positions = _position_lookup(sweepstake)
    rows: list[dict] = []
    for part in sweepstake.participants:
        alloc = part.allocation
        if not alloc or not alloc.team:
            continue
        team = alloc.team
        rows.append({
            "participant_id": part.id,
            "participant_name": part.user.username,
            "avatar_color": part.user.avatar_color,
            "team_name": team.name,
            "flag_emoji": team.flag_emoji,
            "stage": team.stage,
            "points": points_for(team, positions),
            "eliminated": team.eliminated,
        })
    rows.sort(key=lambda r: (-r["points"], r["participant_name"]))
    pool = sweepstake.prize_pool
    tiers = {t.rank: t.percentage for t in sweepstake.prize_tiers}
    result: list[LeaderboardRow] = []
    for i, r in enumerate(rows, start=1):
        payout = pool * tiers.get(i, 0) / 100
        result.append(LeaderboardRow(rank=i, potential_payout=round(payout, 2), **r))
    return result

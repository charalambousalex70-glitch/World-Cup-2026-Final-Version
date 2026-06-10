"""Scoring rules and leaderboard computation.

Points are awarded purely by the furthest stage a participant's team has
reached. Recomputed any time fixtures change.
"""
from app.models import Sweepstake
from app.schemas import LeaderboardRow

STAGE_POINTS: dict[str, int] = {
    "Group": 10,
    "R16": 25,
    "QF": 45,
    "SF": 70,
    "Final": 90,
    "Winner": 120,
    "Out": 0,
}


def points_for_stage(stage: str) -> int:
    return STAGE_POINTS.get(stage, 0)


def compute_leaderboard(sweepstake: Sweepstake) -> list[LeaderboardRow]:
    """Build a ranked leaderboard from current allocations + team stages.

    `sweepstake` must be loaded with participants -> user, allocation -> team,
    and prize_tiers.
    """
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
            "points": points_for_stage(team.stage),
            "eliminated": team.eliminated,
        })

    # Highest points first; stable by name for ties.
    rows.sort(key=lambda r: (-r["points"], r["participant_name"]))

    pool = sweepstake.prize_pool
    tiers = {t.rank: t.percentage for t in sweepstake.prize_tiers}

    result: list[LeaderboardRow] = []
    for i, r in enumerate(rows, start=1):
        payout = pool * tiers.get(i, 0) / 100
        result.append(LeaderboardRow(rank=i, potential_payout=round(payout, 2), **r))
    return result

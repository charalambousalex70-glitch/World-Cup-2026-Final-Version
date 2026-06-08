"""Football data integration (football-data.org v4).

Responsible for:
- importing the team list for a competition when a sweepstake is created
- polling fixtures/results and mapping them onto our Fixture + Team rows
- deriving each team's furthest stage so scoring stays current

If FOOTBALL_API_KEY is empty the client runs in "offline" mode and the
seed/sample data is used instead — handy for local dev and the demo.
"""
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Fixture, Sweepstake, Team

# Map football-data.org stage labels to our compact stage codes.
STAGE_MAP = {
    "GROUP_STAGE": "Group",
    "LAST_16": "R16",
    "QUARTER_FINALS": "QF",
    "SEMI_FINALS": "SF",
    "FINAL": "Final",
    "3RD_PLACE": "Final",
}
STAGE_ORDER = ["Group", "R16", "QF", "SF", "Final", "Winner"]

# Quick flag lookup for common nations (extend as needed).
FLAGS = {
    "Brazil": "🇧🇷", "France": "🇫🇷", "England": "🏴", "Argentina": "🇦🇷",
    "Spain": "🇪🇸", "Germany": "🇩🇪", "Portugal": "🇵🇹", "Netherlands": "🇳🇱",
    "Belgium": "🇧🇪", "Croatia": "🇭🇷", "Italy": "🇮🇹", "USA": "🇺🇸",
    "Mexico": "🇲🇽", "Canada": "🇨🇦", "Japan": "🇯🇵", "Morocco": "🇲🇦",
}


def _headers() -> dict:
    return {"X-Auth-Token": settings.FOOTBALL_API_KEY}


def is_offline() -> bool:
    return not settings.FOOTBALL_API_KEY


async def fetch_teams(competition_code: str) -> list[dict]:
    """Return [{external_id, name, flag_emoji, crest_url}] for a competition."""
    if is_offline():
        return []
    url = f"{settings.FOOTBALL_API_URL}/competitions/{competition_code}/teams"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=_headers())
        r.raise_for_status()
        data = r.json()
    return [
        {
            "external_id": str(t["id"]),
            "name": t["name"],
            "flag_emoji": FLAGS.get(t["name"], "🏳️"),
            "crest_url": t.get("crest"),
        }
        for t in data.get("teams", [])
    ]


async def sync_fixtures(db: AsyncSession, sweepstake: Sweepstake) -> list[Fixture]:
    """Pull matches for the sweepstake's competition and upsert Fixture rows.

    Returns the list of fixtures whose score/status actually changed, so the
    caller can broadcast just the deltas.
    """
    if is_offline() or not sweepstake.competition_code:
        return []

    url = f"{settings.FOOTBALL_API_URL}/competitions/{sweepstake.competition_code}/matches"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=_headers())
        r.raise_for_status()
        matches = r.json().get("matches", [])

    existing = {
        f.external_id: f
        for f in (
            await db.execute(
                select(Fixture).where(Fixture.sweepstake_id == sweepstake.id)
            )
        ).scalars().all()
    }

    changed: list[Fixture] = []
    for m in matches:
        ext = str(m["id"])
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        score = m.get("score", {}).get("fullTime", {})
        hs, as_ = score.get("home"), score.get("away")
        status = m["status"]  # SCHEDULED|TIMED|IN_PLAY|PAUSED|FINISHED
        norm_status = "LIVE" if status in ("IN_PLAY", "PAUSED") else (
            "FINISHED" if status == "FINISHED" else "SCHEDULED"
        )
        stage = STAGE_MAP.get(m.get("stage", ""), "Group")
        kickoff = _parse_dt(m.get("utcDate"))

        fx = existing.get(ext)
        if fx is None:
            fx = Fixture(sweepstake_id=sweepstake.id, external_id=ext)
            db.add(fx)
            changed.append(fx)
        elif (fx.home_score, fx.away_score, fx.status) != (hs, as_, norm_status):
            changed.append(fx)

        fx.home_team, fx.away_team = home, away
        fx.home_score, fx.away_score = hs, as_
        fx.status, fx.stage = norm_status, stage
        fx.kickoff = kickoff

    await db.flush()
    if changed:
        await _recompute_team_stages(db, sweepstake)
    return changed


async def _recompute_team_stages(db: AsyncSession, sweepstake: Sweepstake) -> None:
    """Derive each team's furthest reached stage from finished fixtures.

    A team that lost a knockout match is marked eliminated at that round; a team
    that won the Final becomes 'Winner'.
    """
    teams = (
        await db.execute(select(Team).where(Team.sweepstake_id == sweepstake.id))
    ).scalars().all()
    fixtures = (
        await db.execute(
            select(Fixture).where(
                Fixture.sweepstake_id == sweepstake.id, Fixture.status == "FINISHED"
            )
        )
    ).scalars().all()

    by_name = {t.name: t for t in teams}
    for fx in fixtures:
        if fx.home_score is None or fx.away_score is None:
            continue
        winner = fx.home_team if fx.home_score > fx.away_score else fx.away_team
        loser = fx.away_team if winner == fx.home_team else fx.home_team

        # Advance the winner's stage to at least the next round.
        w = by_name.get(winner)
        if w and not w.eliminated:
            _advance(w, fx.stage)

        # Knockout loser is eliminated (group results don't eliminate here).
        if fx.stage != "Group":
            l = by_name.get(loser)
            if l:
                l.stage = fx.stage  # reached this stage
                l.eliminated = True
    await db.flush()


def _advance(team: Team, fixture_stage: str) -> None:
    """Move team to the round *after* the one it just won."""
    if fixture_stage == "Final":
        team.stage = "Winner"
        return
    try:
        idx = STAGE_ORDER.index(fixture_stage)
        team.stage = STAGE_ORDER[min(idx + 1, len(STAGE_ORDER) - 1)]
    except ValueError:
        pass


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

"""Football data integration (football-data.org v4).

Responsible for:
- importing the team list for a competition when a sweepstake is created
- polling fixtures/results and mapping them onto our Fixture + Team rows
- deriving each team's furthest stage so scoring stays current

If FOOTBALL_API_KEY is empty the client runs in "offline" mode and the
seed/sample data is used instead — handy for local dev and the demo.
"""
from datetime import datetime, timezone

import json as _json
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

# The football API uses slightly different country names than our ranked list.
# Normalise API names → our names so team-stage derivation matches correctly.
NAME_ALIASES = {
    "United States": "USA",
    "USA": "USA",
    "Bosnia-Herzegovina": "Bosnia & Herzegovina",
    "Bosnia and Herzegovina": "Bosnia & Herzegovina",
    "Korea Republic": "South Korea",
    "Republic of Korea": "South Korea",
    "South Korea": "South Korea",
    "IR Iran": "Iran",
    "Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast",
    "Ivory Coast": "Ivory Coast",
    "Turkey": "Türkiye",
    "Türkiye": "Türkiye",
    "Czech Republic": "Czechia",
    "Czechia": "Czechia",
    "Cabo Verde": "Cape Verde",
    "Cape Verde": "Cape Verde",
    "Curaçao": "Curacao",
    "Curacao": "Curacao",
}


def _norm_name(name: str | None) -> str:
    if not name:
        return "TBD"
    return NAME_ALIASES.get(name, name)


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
    """Return [{external_id, name, flag_emoji, crest_url}] for a competition.

    Never raises: if the football API is slow, rate-limited, or the competition
    code is unknown, we return [] so the caller falls back to the sample teams
    and sweepstake creation still succeeds quickly.
    """
    if is_offline() or not competition_code:
        return []
    url = f"{settings.FOOTBALL_API_URL}/competitions/{competition_code}/teams"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, headers=_headers())
            r.raise_for_status()
            data = r.json()
    except Exception:
        # Slow/failed external API must not block sweepstake creation.
        return []
    return [
        {
            "external_id": str(t["id"]),
            "name": t["name"],
            "flag_emoji": FLAGS.get(t["name"], "🏳️"),
            "crest_url": t.get("crest"),
        }
        for t in data.get("teams", [])
    ]


async def fetch_standings(competition_code: str) -> dict:
    """Return group standings keyed by group name.

    {"Group A": [{"team","played","won","draw","lost","gf","ga","gd","points","position"}], ...}
    Never raises — returns {} if the API/tier doesn't provide standings.
    """
    if is_offline() or not competition_code:
        return {}
    url = f"{settings.FOOTBALL_API_URL}/competitions/{competition_code}/standings"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers=_headers())
            r.raise_for_status()
            data = r.json()
    except Exception:
        return {}
    groups: dict = {}
    for st in data.get("standings", []):
        if st.get("type") not in ("TOTAL", None):
            continue
        gname = st.get("group") or "Group"
        gname = gname.replace("GROUP_", "Group ").title() if gname.startswith("GROUP") else gname
        table = []
        for row in st.get("table", []):
            team = (row.get("team") or {}).get("name")
            table.append({
                "team": _norm_name(team),
                "position": row.get("position"),
                "played": row.get("playedGames"),
                "won": row.get("won"), "draw": row.get("draw"), "lost": row.get("lost"),
                "gf": row.get("goalsFor"), "ga": row.get("goalsAgainst"),
                "gd": row.get("goalDifference"), "points": row.get("points"),
            })
        if table:
            groups[gname] = table
    return groups


async def sync_fixtures(db: AsyncSession, sweepstake: Sweepstake) -> list[Fixture]:
    """Pull matches for the sweepstake's competition and upsert Fixture rows.

    Returns the list of fixtures whose score/status actually changed, so the
    caller can broadcast just the deltas.
    """
    if is_offline() or not sweepstake.competition_code:
        return []

    url = f"{settings.FOOTBALL_API_URL}/competitions/{sweepstake.competition_code}/matches"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers=_headers())
            r.raise_for_status()
            matches = r.json().get("matches", [])
    except Exception:
        # Slow/failed/rate-limited API: skip this cycle quietly.
        return []

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
        # Knockout matches that haven't been decided yet come back with null
        # teams. Use a placeholder so the NOT NULL column is satisfied; the
        # poller will fill in real names once earlier rounds finish.
        home = _norm_name((m.get("homeTeam") or {}).get("name"))
        away = _norm_name((m.get("awayTeam") or {}).get("name"))
        score = m.get("score", {}).get("fullTime", {})
        hs, as_ = score.get("home"), score.get("away")
        status = m["status"]  # SCHEDULED|TIMED|IN_PLAY|PAUSED|FINISHED
        norm_status = "LIVE" if status in ("IN_PLAY", "PAUSED") else (
            "FINISHED" if status == "FINISHED" else "SCHEDULED"
        )
        stage = STAGE_MAP.get(m.get("stage", ""), "Group")
        kickoff = _parse_dt(m.get("utcDate"))
        venue = m.get("venue") or None
        refs = m.get("referees") or []
        referee = refs[0].get("name") if refs and isinstance(refs[0], dict) else None
        # Compact detail JSON: goalscorers + halftime score when provided.
        goals = [
            {"minute": g.get("minute"), "scorer": (g.get("scorer") or {}).get("name"),
             "team": (g.get("team") or {}).get("name"),
             "assist": (g.get("assist") or {}).get("name")}
            for g in (m.get("goals") or [])
        ]
        ht = (m.get("score", {}).get("halfTime") or {})
        detail = _json.dumps({"goals": goals, "ht": [ht.get("home"), ht.get("away")]}) if (goals or ht.get("home") is not None) else None

        fx = existing.get(ext)
        if fx is None:
            fx = Fixture(sweepstake_id=sweepstake.id, external_id=ext)
            db.add(fx)
            changed.append({"fx": fx, "prev_status": None, "prev_hs": None,
                            "prev_as": None, "prev_goals": 0})
        elif (fx.home_score, fx.away_score, fx.status, fx.home_team, fx.away_team) != (hs, as_, norm_status, home, away):
            prev_goals = 0
            try:
                prev_goals = len((_json.loads(fx.detail) or {}).get("goals", [])) if fx.detail else 0
            except Exception:
                prev_goals = 0
            changed.append({"fx": fx, "prev_status": fx.status,
                            "prev_hs": fx.home_score, "prev_as": fx.away_score,
                            "prev_goals": prev_goals})

        fx.home_team, fx.away_team = home, away
        fx.home_score, fx.away_score = hs, as_
        fx.status, fx.stage = norm_status, stage
        fx.kickoff = kickoff
        fx.venue = venue
        fx.referee = referee
        fx.detail = detail

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

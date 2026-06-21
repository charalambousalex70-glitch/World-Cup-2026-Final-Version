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
import logging
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Fixture, Sweepstake, Team

log = logging.getLogger("football")

# --- Self-imposed rate limit -------------------------------------------------
# football-data.org's free/livescores tier allows 10 requests/minute. We cap
# ourselves at 8/min across ALL call sites (poller, on-demand refresh, debug)
# so a burst can never trip an auto-suspension again.
import asyncio as _asyncio
import time as _time

_CALL_TIMES: list[float] = []
_RATE_MAX = 8
_RATE_WINDOW = 60.0
_rate_lock = _asyncio.Lock()


async def _rate_gate():
    """Block just long enough to stay under _RATE_MAX calls per _RATE_WINDOW."""
    async with _rate_lock:
        now = _time.monotonic()
        # Drop timestamps older than the window.
        cutoff = now - _RATE_WINDOW
        while _CALL_TIMES and _CALL_TIMES[0] < cutoff:
            _CALL_TIMES.pop(0)
        if len(_CALL_TIMES) >= _RATE_MAX:
            wait = _RATE_WINDOW - (now - _CALL_TIMES[0]) + 0.05
            if wait > 0:
                await _asyncio.sleep(wait)
        _CALL_TIMES.append(_time.monotonic())

# Stage structure now lives in one place: app.services.tournament.
from app.services.tournament import STAGE_MAP, STAGE_ORDER

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
    # X-Api-Version v4.1 unlocks the live `minute` and `injuryTime` fields.
    # It's backward-safe: existing fields are unchanged, and the new fields are
    # simply absent (handled as null downstream) on matches that don't have them.
    return {"X-Auth-Token": settings.FOOTBALL_API_KEY, "X-Api-Version": "v4.1"}


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
        await _rate_gate()
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
        await _rate_gate()
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers=_headers())
            r.raise_for_status()
            data = r.json()
    except Exception:
        return {}
    groups: dict = {}
    for st in data.get("standings", []):
        # WC group standings come as type TOTAL with a group like "GROUP_A".
        if st.get("type") not in ("TOTAL", None):
            continue
        raw_group = st.get("group")
        if raw_group:
            gname = raw_group.replace("GROUP_", "Group ").replace("_", " ")
            if not gname.startswith("Group"):
                gname = gname[:1].upper() + gname[1:]
        else:
            gname = "Standings"
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
        if table and (gname not in groups or len(table) > len(groups[gname])):
            groups[gname] = table
    return groups


# Feed health, surfaced to admins. Updated by every fetch attempt.
FEED_HEALTH = {
    "last_status": None,        # last HTTP status seen
    "last_ok": None,            # ISO timestamp of last 200
    "last_error_message": None, # provider's message on failure
    "consecutive_failures": 0,
}


def _record_feed(status: int | None, message: str | None = None):
    from datetime import datetime, timezone
    FEED_HEALTH["last_status"] = status
    if status == 200:
        FEED_HEALTH["last_ok"] = datetime.now(timezone.utc).isoformat()
        FEED_HEALTH["consecutive_failures"] = 0
        FEED_HEALTH["last_error_message"] = None
    else:
        FEED_HEALTH["consecutive_failures"] += 1
        if message:
            FEED_HEALTH["last_error_message"] = message[:300]


async def fetch_matches(competition_code: str) -> list:
    """Fetch raw match list for a competition once (so it can be shared across
    all leagues on that competition in a single poll cycle).

    Records feed health and does NOT silently swallow auth/permission errors —
    a 403 means the plan isn't authorised for this competition.
    """
    if is_offline() or not competition_code:
        return []
    url = f"{settings.FOOTBALL_API_URL}/competitions/{competition_code}/matches"
    try:
        await _rate_gate()
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers=_headers())
            if r.status_code != 200:
                msg = None
                try:
                    msg = r.json().get("message")
                except Exception:
                    msg = r.text[:200]
                _record_feed(r.status_code, msg)
                log.warning("matches fetch %s for %s: %s", r.status_code, competition_code, msg)
                return []
            _record_feed(200)
            return r.json().get("matches", [])
    except Exception as e:
        _record_feed(None, str(e))
        return []


async def sync_fixtures(db: AsyncSession, sweepstake: Sweepstake, matches=None) -> list[Fixture]:
    """Pull matches for the sweepstake's competition and upsert Fixture rows.

    Returns the list of fixtures whose score/status actually changed, so the
    caller can broadcast just the deltas. If `matches` is provided (pre-fetched
    by the caller), no network call is made — this lets one fetch serve every
    league on the same competition in the same cycle.
    """
    if is_offline() or not sweepstake.competition_code:
        return []

    if matches is None:
        matches = await fetch_matches(sweepstake.competition_code)
    if not matches:
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
        # Live clock (v4.1): present only on in-play matches; absent/null otherwise.
        minute = m.get("minute")
        injury = m.get("injuryTime")
        try:
            minute = int(minute) if minute is not None else None
        except (TypeError, ValueError):
            minute = None
        try:
            injury = int(injury) if injury not in (None, 0, "0") else None
        except (TypeError, ValueError):
            injury = None
        # 'winner' from the feed is HOME_TEAM / AWAY_TEAM / DRAW and already
        # reflects penalty-shootout outcomes in knockout ties.
        winner_code = (m.get("score") or {}).get("winner")
        detail = _json.dumps({
            "goals": goals,
            "ht": [ht.get("home"), ht.get("away")],
            "winner": winner_code,
            "minute": minute,
            "injury": injury,
            "paused": status == "PAUSED",
        }) if (goals or ht.get("home") is not None or winner_code or minute is not None or status == "PAUSED") else None

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
        else:
            # Score/status unchanged but the live minute may have ticked — update
            # the stored detail quietly without adding to `changed` (no goal-bot
            # spam), so the next fixtures payload carries a fresh clock.
            if norm_status == "LIVE" and fx.detail:
                try:
                    d = _json.loads(fx.detail) or {}
                    if d.get("minute") != minute or d.get("injury") != injury:
                        d["minute"], d["injury"] = minute, injury
                        fx.detail = _json.dumps(d)
                except Exception:
                    pass

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
    """Derive each team's current stage from the fixtures it appears in.

    A team's stage = the furthest knockout round it has a fixture in (being
    scheduled for the R16 means it qualified and is 'in the R16'). A team that
    LOST a knockout tie is eliminated at the round it lost in. Winning the Final
    makes it 'Winner'. Everyone stays 'Group' through the group phase regardless
    of group results.
    """
    teams = (
        await db.execute(select(Team).where(Team.sweepstake_id == sweepstake.id))
    ).scalars().all()
    all_fx = (
        await db.execute(select(Fixture).where(Fixture.sweepstake_id == sweepstake.id))
    ).scalars().all()

    by_name = {t.name: t for t in teams}
    # Reset, then promote based on the rounds each team is scheduled in.
    for t in teams:
        t.stage = "Group"
        t.eliminated = False

    for fx in all_fx:
        rnd = fx.stage  # Group | R32 | R16 | QF | SF | Final | 3rd_playoff
        if rnd == "Group":
            continue
        # The 3rd-place play-off doesn't sit on the main ladder; handle it below.
        if rnd != "3rd_playoff":
            for side in (fx.home_team, fx.away_team):
                t = by_name.get(side)
                if not t:
                    continue
                try:
                    if STAGE_ORDER.index(rnd) > STAGE_ORDER.index(t.stage):
                        t.stage = rnd
                except ValueError:
                    pass

        # Apply finished knockout results: winner advances, loser is out here.
        if fx.status == "FINISHED" and fx.home_score is not None and fx.away_score is not None:
            winner = None
            if fx.home_score > fx.away_score:
                winner = fx.home_team
            elif fx.away_score > fx.home_score:
                winner = fx.away_team
            else:
                code = None
                if fx.detail:
                    try:
                        code = (_json.loads(fx.detail) or {}).get("winner")
                    except Exception:
                        code = None
                if code == "HOME_TEAM":
                    winner = fx.home_team
                elif code == "AWAY_TEAM":
                    winner = fx.away_team
            if winner:
                loser = fx.away_team if winner == fx.home_team else fx.home_team
                w, l = by_name.get(winner), by_name.get(loser)
                if rnd == "3rd_playoff":
                    # Decides 3rd (winner) vs 4th (loser); both are eliminated.
                    if w: w.stage = "3rd"; w.eliminated = True
                    if l: l.stage = "4th"; l.eliminated = True
                elif rnd == "Final":
                    # Champion vs Runner-up.
                    if w: w.stage = "Winner"; w.eliminated = False
                    if l: l.stage = "Runner-up"; l.eliminated = True
                else:
                    if w:
                        _advance(w, rnd)        # into the next round
                    if l:
                        l.stage = rnd           # eliminated at the round they lost
                        l.eliminated = True
    await db.flush()


def _advance(team: Team, fixture_stage: str) -> None:
    """Move a team to the round AFTER the knockout round it just won.

    Group matches never change the stage. Winning the Final → 'Winner'.
    Winning R16 → 'QF', QF → 'SF', SF → 'Final'.
    """
    if fixture_stage in ("Group", ""):
        return
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

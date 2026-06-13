"""Background task that polls the football API and pushes live updates.

Runs as an asyncio task started on app startup. For every active sweepstake it
syncs fixtures; when anything changes it recomputes the leaderboard, writes
notifications, and broadcasts over WebSockets.

In offline mode (no API key) it idles — the demo/seed data is static.
"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import Allocation, Fixture, Notification, Participant, Sweepstake
from app.services import football
from app.services.scoring import compute_leaderboard
from app.websocket.manager import manager

log = logging.getLogger("poller")

# Lightweight runtime stats so a diagnostics endpoint can confirm the poller is
# actually running on the server (vs. silently stopped or never started).
POLLER_STATS = {
    "started": False,
    "last_run": None,        # ISO timestamp of the last completed cycle
    "last_changes": 0,       # fixtures changed in the last cycle
    "cycles": 0,
    "last_error": None,
}


async def poll_loop() -> None:
    if football.is_offline():
        log.info("Football API offline — poller idle.")
        return
    POLLER_STATS["started"] = True
    log.info("Football poller started (every %ss).", settings.FOOTBALL_POLL_SECONDS)
    while True:
        try:
            await _poll_once()
        except Exception as e:  # never let the loop die
            POLLER_STATS["last_error"] = str(e)
            log.exception("Poll cycle failed")
        from datetime import datetime, timezone
        POLLER_STATS["last_run"] = datetime.now(timezone.utc).isoformat()
        POLLER_STATS["cycles"] += 1
        await asyncio.sleep(settings.FOOTBALL_POLL_SECONDS)


async def _poll_once() -> None:
    async with AsyncSessionLocal() as db:
        active = (
            await db.execute(
                select(Sweepstake).where(
                    Sweepstake.competition_code.isnot(None),
                    Sweepstake.status.in_(["open", "drawn", "active"]),
                )
            )
        ).scalars().all()

        # Fetch each competition's feed ONCE, then apply to every league on it.
        # This means all leagues update from the same snapshot simultaneously
        # (no per-league lag) and we make far fewer API calls.
        import json as _json
        codes = {s.competition_code for s in active if s.competition_code}
        matches_by_code: dict = {}
        standings_by_code: dict = {}
        for code in codes:
            matches_by_code[code] = await football.fetch_matches(code)
            standings_by_code[code] = await football.fetch_standings(code)

        for sweep in active:
            sweep_id = sweep.id  # capture before any further loads
            try:
                shared_matches = matches_by_code.get(sweep.competition_code)
                changes = await football.sync_fixtures(db, sweep, matches=shared_matches)

                # Apply the shared standings snapshot (already fetched once).
                standings = standings_by_code.get(sweep.competition_code)
                if standings:
                    sweep.standings = _json.dumps(standings)
                    await db.flush()

                if not changes:
                    continue

                # Capture changed fixtures + their PREVIOUS state into plain dicts
                # now (avoids async lazy-load issues, and lets us announce only
                # genuine transitions rather than re-posting every poll).
                changed_data = []
                for ch in changes:
                    fx = ch["fx"]
                    try:
                        goals_now = (_json.loads(fx.detail) or {}).get("goals", []) if fx.detail else []
                    except Exception:
                        goals_now = []
                    changed_data.append({
                        "home": fx.home_team, "away": fx.away_team,
                        "hs": fx.home_score, "as_": fx.away_score,
                        "status": fx.status, "stage": fx.stage,
                        "prev_status": ch["prev_status"], "prev_goals": ch["prev_goals"],
                        "goals": goals_now,
                    })

                full = (
                    await db.execute(
                        select(Sweepstake)
                        .where(Sweepstake.id == sweep_id)
                        .options(
                            selectinload(Sweepstake.participants).selectinload(Participant.user),
                            selectinload(Sweepstake.participants)
                            .selectinload(Participant.allocation)
                            .selectinload(Allocation.team),
                            selectinload(Sweepstake.prize_tiers),
                        )
                    )
                ).scalar_one()

                parts = [
                    (p.user_id, p.allocation.team.name if (p.allocation and p.allocation.team) else None)
                    for p in full.participants
                ]

                fixtures = (
                    await db.execute(select(Fixture).where(Fixture.sweepstake_id == sweep_id))
                ).scalars().all()
                board = compute_leaderboard(full, fixtures)
                await manager.broadcast(
                    str(sweep_id), "leaderboard_updated",
                    {"leaderboard": [r.model_dump() for r in board]},
                )

                # ---- Goal Bot: ONLY kick-off, each new goal, red cards, full-time.
                # Deduped by comparing against the previous state of each fixture.
                from app.models import Comment

                def _emit(msg):
                    bot = Comment(sweepstake_id=sweep_id, user_id=None, body=msg[:500])
                    db.add(bot)
                    return bot

                bot_msgs = []
                for fxd in changed_data:
                    home, away = fxd["home"], fxd["away"]
                    hs, as_ = fxd.get("hs") or 0, fxd.get("as_") or 0
                    prev, cur = fxd["prev_status"], fxd["status"]

                    # Kick-off: SCHEDULED -> LIVE
                    if cur == "LIVE" and prev in (None, "SCHEDULED"):
                        bot_msgs.append(f"🟢 Kick-off! {home} vs {away}")

                    # New goals: announce each goal added since last poll, with scorer.
                    if cur in ("LIVE", "FINISHED"):
                        new_goals = fxd["goals"][fxd["prev_goals"]:] if fxd["goals"] else []
                        for g in new_goals:
                            who = g.get("scorer") or "Goal"
                            mins = f"{g['minute']}'" if g.get("minute") is not None else ""
                            tm = g.get("team") or ""
                            bot_msgs.append(f"⚽ GOAL {mins} {who} ({tm}) — {home} {hs}–{as_} {away}".replace("  ", " "))
                        # Red cards (only if the feed provides them; usually absent on free tier)
                        for rc in (fxd.get("reds") or []):
                            bot_msgs.append(f"🟥 Red card: {rc}")

                    # Full-time: -> FINISHED (announce once)
                    if cur == "FINISHED" and prev != "FINISHED":
                        bot_msgs.append(f"🏁 Full time: {home} {hs}–{as_} {away}")

                for msg in bot_msgs:
                    bot = _emit(msg)
                    await db.flush()
                    await manager.broadcast(str(sweep_id), "comment_added", {
                        "id": str(bot.id), "body": bot.body,
                        "created_at": bot.created_at.isoformat() if bot.created_at else None,
                        "username": "⚽ Goal Bot", "avatar_color": "#ffc83d", "reactions": {},
                    })

                # Build per-user notifications only on full-time results.
                for fxd in changed_data:
                    if fxd["status"] != "FINISHED" or fxd["prev_status"] == "FINISHED":
                        continue
                    for user_id, tname in parts:
                        if not tname or tname not in (fxd["home"], fxd["away"]):
                            continue
                        won = (
                            (tname == fxd["home"] and (fxd["hs"] or 0) > (fxd["as_"] or 0))
                            or (tname == fxd["away"] and (fxd["as_"] or 0) > (fxd["hs"] or 0))
                        )
                        db.add(Notification(
                            user_id=user_id, sweepstake_id=sweep_id,
                            icon="⚽" if won else "❌",
                            title=f"{tname} {'won' if won else 'lost'} {fxd['hs']}–{fxd['as_']}",
                            body=f"{fxd['home']} vs {fxd['away']} · {fxd['stage']}",
                        ))
                await db.commit()
                await manager.broadcast(str(sweep_id), "fixtures_updated", {"count": len(changed_data)})
            except Exception:
                # One bad sweepstake must not kill the whole poll cycle.
                log.exception("Failed to process sweepstake %s", sweep_id)
                await db.rollback()

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
from app.models import Allocation, Notification, Participant, Sweepstake
from app.services import football
from app.services.scoring import compute_leaderboard
from app.websocket.manager import manager

log = logging.getLogger("poller")


async def poll_loop() -> None:
    if football.is_offline():
        log.info("Football API offline — poller idle.")
        return
    log.info("Football poller started (every %ss).", settings.FOOTBALL_POLL_SECONDS)
    while True:
        try:
            await _poll_once()
        except Exception:  # never let the loop die
            log.exception("Poll cycle failed")
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

        for sweep in active:
            sweep_id = sweep.id  # capture before any further loads
            try:
                changed = await football.sync_fixtures(db, sweep)
                if not changed:
                    continue

                # Capture the changed fixtures' fields into plain dicts NOW,
                # so later commits can't expire them and trigger lazy reloads
                # (the source of the async "MissingGreenlet" error).
                changed_data = [
                    {
                        "home": fx.home_team, "away": fx.away_team,
                        "hs": fx.home_score, "as_": fx.away_score,
                        "status": fx.status, "stage": fx.stage,
                    }
                    for fx in changed
                ]

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

                # Snapshot participant -> (user_id, team_name) eagerly.
                parts = [
                    (p.user_id, p.allocation.team.name if (p.allocation and p.allocation.team) else None)
                    for p in full.participants
                ]

                board = compute_leaderboard(full)
                await manager.broadcast(
                    str(sweep_id), "leaderboard_updated",
                    {"leaderboard": [r.model_dump() for r in board]},
                )

                # Build notifications from the captured plain data.
                for fxd in changed_data:
                    if fxd["status"] != "FINISHED":
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

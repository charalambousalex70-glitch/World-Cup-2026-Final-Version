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
                select(Sweepstake).where(Sweepstake.status == "active")
            )
        ).scalars().all()

        for sweep in active:
            changed = await football.sync_fixtures(db, sweep)
            if not changed:
                continue

            full = (
                await db.execute(
                    select(Sweepstake)
                    .where(Sweepstake.id == sweep.id)
                    .options(
                        selectinload(Sweepstake.participants).selectinload(Participant.user),
                        selectinload(Sweepstake.participants)
                        .selectinload(Participant.allocation)
                        .selectinload(Allocation.team),
                        selectinload(Sweepstake.prize_tiers),
                    )
                )
            ).scalar_one()

            board = compute_leaderboard(full)
            await manager.broadcast(
                str(sweep.id), "leaderboard_updated",
                {"leaderboard": [r.model_dump() for r in board]},
            )

            # Notify each participant whose team result changed.
            for fx in changed:
                for part in full.participants:
                    alloc = part.allocation
                    if not alloc or not alloc.team:
                        continue
                    tname = alloc.team.name
                    if tname in (fx.home_team, fx.away_team) and fx.status == "FINISHED":
                        won = (
                            (tname == fx.home_team and (fx.home_score or 0) > (fx.away_score or 0))
                            or (tname == fx.away_team and (fx.away_score or 0) > (fx.home_score or 0))
                        )
                        db.add(Notification(
                            user_id=part.user_id, sweepstake_id=sweep.id,
                            icon="⚽" if won else "❌",
                            title=f"{tname} {'won' if won else 'lost'} {fx.home_score}–{fx.away_score}",
                            body=f"{fx.home_team} vs {fx.away_team} · {fx.stage}",
                        ))
            await db.commit()
            await manager.broadcast(str(sweep.id), "fixtures_updated", {"count": len(changed)})

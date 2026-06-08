"""Team draw engine.

Guarantees:
- one team per participant
- no duplicate team assignments
- cryptographically-seeded shuffle for fairness
- idempotency: refuses to run if the draw is already approved
"""
import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Allocation, Participant, Sweepstake, Team


class DrawError(Exception):
    pass


async def run_draw(db: AsyncSession, sweepstake: Sweepstake) -> list[Allocation]:
    """Randomly allocate teams to participants. Returns the new allocations.

    This clears any *unapproved* prior draw and regenerates it. Once a draw is
    approved it is immutable and this raises DrawError.
    """
    if sweepstake.draw_approved:
        raise DrawError("Draw already approved and finalized.")

    participants = (
        await db.execute(
            select(Participant).where(Participant.sweepstake_id == sweepstake.id)
        )
    ).scalars().all()
    teams = (
        await db.execute(select(Team).where(Team.sweepstake_id == sweepstake.id))
    ).scalars().all()

    if not participants:
        raise DrawError("No participants to draw for.")
    if len(teams) < len(participants):
        raise DrawError(
            f"Not enough teams ({len(teams)}) for participants ({len(participants)})."
        )

    # Wipe any previous unapproved allocations.
    await db.execute(
        delete(Allocation).where(Allocation.sweepstake_id == sweepstake.id)
    )

    # Secure shuffle of the team pool.
    pool = list(teams)
    _secure_shuffle(pool)

    allocations: list[Allocation] = []
    for participant, team in zip(participants, pool):
        alloc = Allocation(
            sweepstake_id=sweepstake.id,
            participant_id=participant.id,
            team_id=team.id,
        )
        db.add(alloc)
        allocations.append(alloc)

    sweepstake.status = "drawn"
    await db.flush()
    return allocations


async def approve_draw(db: AsyncSession, sweepstake: Sweepstake) -> None:
    """Lock the draw permanently."""
    existing = (
        await db.execute(
            select(Allocation).where(Allocation.sweepstake_id == sweepstake.id)
        )
    ).scalars().all()
    if not existing:
        raise DrawError("Nothing to approve — run the draw first.")
    sweepstake.draw_approved = True
    sweepstake.status = "active"
    await db.flush()


def _secure_shuffle(items: list) -> None:
    """In-place Fisher–Yates using a CSPRNG."""
    for i in range(len(items) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        items[i], items[j] = items[j], items[i]

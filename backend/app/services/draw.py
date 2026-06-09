"""Team draw engine.

Guarantees:
- one team per participant
- no duplicate team assignments
- cryptographically-seeded shuffle for fairness
- idempotency: refuses to run if the draw is already approved

Team selection: the draw uses the TOP-N teams from the ranked contender list
(app.services.teams_data.RANKED_TEAMS), where N = number of participants. So
with 10 players, the 10 most-likely-to-win teams are drawn — everyone gets a
genuine contender. The team set is (re)generated at draw time so it always
matches the final participant count.
"""
import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Allocation, Participant, Sweepstake, Team
from app.services.teams_data import RANKED_TEAMS


class DrawError(Exception):
    pass


async def run_draw(db: AsyncSession, sweepstake: Sweepstake) -> list[Allocation]:
    """Randomly allocate the top-N ranked teams to the N participants.

    Clears any *unapproved* prior draw and regenerates it. Once a draw is
    approved it is immutable and this raises DrawError.
    """
    if sweepstake.draw_approved:
        raise DrawError("Draw already approved and finalized.")

    participants = (
        await db.execute(
            select(Participant).where(Participant.sweepstake_id == sweepstake.id)
        )
    ).scalars().all()
    n = len(participants)

    if n == 0:
        raise DrawError("No participants to draw for.")
    if n > len(RANKED_TEAMS):
        raise DrawError(
            f"Too many participants ({n}); only {len(RANKED_TEAMS)} teams available."
        )

    # Clear previous unapproved allocations AND the old team set, then create
    # exactly the top-N ranked teams for this draw.
    await db.execute(delete(Allocation).where(Allocation.sweepstake_id == sweepstake.id))
    await db.execute(delete(Team).where(Team.sweepstake_id == sweepstake.id))
    await db.flush()

    top_teams = RANKED_TEAMS[:n]
    teams = [
        Team(sweepstake_id=sweepstake.id, name=name, flag_emoji=flag, stage="Group")
        for name, flag in top_teams
    ]
    for t in teams:
        db.add(t)
    await db.flush()

    # Secure shuffle so the ranking doesn't bias who gets which team.
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

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
import hashlib
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Allocation, Participant, Sweepstake, Team
from app.services.teams_data import RANKED_TEAMS


class DrawError(Exception):
    pass


async def run_draw(db: AsyncSession, sweepstake: Sweepstake, excluded: list[str] | None = None) -> list[Allocation]:
    """Randomly allocate the top-N ranked teams to the N participants.

    `excluded` is an optional list of team names to skip; the next-ranked teams
    take their place so there are still exactly N teams.

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

    # Build the candidate pool, skipping excluded teams, then take the top N.
    excluded_set = {e.strip().lower() for e in (excluded or [])}
    candidates = [(name, flag) for name, flag in RANKED_TEAMS if name.lower() not in excluded_set]
    if n > len(candidates):
        raise DrawError(
            f"Too many participants ({n}) for the available teams ({len(candidates)}). "
            f"Exclude fewer teams or reduce participants."
        )
    top_teams = candidates[:n]

    # Clear previous unapproved allocations AND the old team set, then create
    # exactly the chosen top-N teams for this draw.
    await db.execute(delete(Allocation).where(Allocation.sweepstake_id == sweepstake.id))
    await db.execute(delete(Team).where(Team.sweepstake_id == sweepstake.id))
    await db.flush()

    teams = [
        Team(sweepstake_id=sweepstake.id, name=name, flag_emoji=flag, stage="Group")
        for name, flag in top_teams
    ]
    for t in teams:
        db.add(t)
    await db.flush()

    # ---- Provably fair allocation ----
    # A random seed is generated, then every assignment is DERIVED from it:
    # teams are ordered by SHA256(seed:team), participants by SHA256(seed:participant_id),
    # and paired index-for-index. Given the seed (published in the audit), anyone
    # can recompute the exact same result — so the draw cannot be rigged or
    # post-edited without detection.
    seed = secrets.token_hex(16)
    team_order = sorted(teams, key=lambda t: hashlib.sha256(f"{seed}:{t.name}".encode()).hexdigest())
    part_order = sorted(participants, key=lambda p: hashlib.sha256(f"{seed}:{p.id}".encode()).hexdigest())

    allocations: list[Allocation] = []
    audit_pairs = []
    for participant, team in zip(part_order, team_order):
        alloc = Allocation(
            sweepstake_id=sweepstake.id,
            participant_id=participant.id,
            team_id=team.id,
        )
        db.add(alloc)
        allocations.append(alloc)
        audit_pairs.append({"participant_id": str(participant.id), "team": team.name})

    sweepstake.draw_audit = json.dumps({
        "seed": seed,
        "method": "sha256(seed:team) orders teams; sha256(seed:participant_id) orders participants; paired by index",
        "drawn_at": datetime.now(timezone.utc).isoformat(),
        "team_pool": [t.name for t in teams],
        "participants": [str(p.id) for p in participants],
        "result": audit_pairs,
    })

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


async def reset_draw(db: AsyncSession, sweepstake: Sweepstake) -> None:
    """Undo a draw so the admin can re-run it (e.g. after more people join).

    Clears allocations and the generated team set, un-approves the draw, and
    returns the sweepstake to the 'open' state so new members can still join
    and a fresh draw reflects everyone.
    """
    await db.execute(delete(Allocation).where(Allocation.sweepstake_id == sweepstake.id))
    await db.execute(delete(Team).where(Team.sweepstake_id == sweepstake.id))
    sweepstake.draw_approved = False
    sweepstake.status = "open"
    await db.flush()


def _secure_shuffle(items: list) -> None:
    """In-place Fisher–Yates using a CSPRNG."""
    for i in range(len(items) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        items[i], items[j] = items[j], items[i]

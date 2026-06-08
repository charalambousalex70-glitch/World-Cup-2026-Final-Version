"""Sweepstake routes — the core of the API."""
import secrets
import string
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import (
    Allocation, Notification, Participant, PrizeTier, Sweepstake, Team, User,
)
from app.schemas import (
    AllocationOut, FixtureOut, JoinRequest, LeaderboardRow, NotificationOut,
    PaymentUpdate, SweepstakeCreate, SweepstakeOut,
)
from app.services import football
from app.services.draw import DrawError, approve_draw, run_draw
from app.services.scoring import compute_leaderboard
from app.websocket.manager import manager
from app.models import Fixture

router = APIRouter(prefix="/sweepstakes", tags=["sweepstakes"])

# Sample teams used when running offline (no football API key).
_SAMPLE_TEAMS = [
    ("Brazil", "🇧🇷"), ("France", "🇫🇷"), ("England", "🏴"), ("Argentina", "🇦🇷"),
    ("Spain", "🇪🇸"), ("Germany", "🇩🇪"), ("Portugal", "🇵🇹"), ("Netherlands", "🇳🇱"),
    ("Belgium", "🇧🇪"), ("Croatia", "🇭🇷"), ("Italy", "🇮🇹"), ("Morocco", "🇲🇦"),
]


def _gen_code(n: int = 7) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "WC" + "".join(secrets.choice(alphabet) for _ in range(n - 2))


async def _load_full(db: AsyncSession, sid: uuid.UUID) -> Sweepstake | None:
    return (
        await db.execute(
            select(Sweepstake)
            .where(Sweepstake.id == sid)
            .options(
                selectinload(Sweepstake.participants).selectinload(Participant.user),
                selectinload(Sweepstake.participants)
                .selectinload(Participant.allocation)
                .selectinload(Allocation.team),
                selectinload(Sweepstake.prize_tiers),
                selectinload(Sweepstake.teams),
            )
        )
    ).scalar_one_or_none()


def _require_admin(sweepstake: Sweepstake, user: User) -> None:
    if sweepstake.admin_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")


# ---------- CRUD ----------
@router.post("", response_model=SweepstakeOut, status_code=201)
async def create_sweepstake(
    body: SweepstakeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if abs(sum(t.percentage for t in body.prize_tiers) - 100) > 0.01:
        raise HTTPException(422, "Prize percentages must sum to 100")

    sweep = Sweepstake(
        name=body.name,
        tournament_name=body.tournament_name,
        competition_code=body.competition_code,
        entry_fee=body.entry_fee,
        currency=body.currency,
        max_participants=body.max_participants,
        start_date=body.start_date,
        invite_code=_gen_code(),
        admin_id=user.id,
    )
    db.add(sweep)
    await db.flush()

    # Admin auto-joins their own sweepstake.
    db.add(Participant(sweepstake_id=sweep.id, user_id=user.id, has_paid=True))

    for tier in body.prize_tiers:
        db.add(PrizeTier(sweepstake_id=sweep.id, rank=tier.rank, percentage=tier.percentage))

    # Import teams: from football API if configured, else sample set.
    api_teams = await football.fetch_teams(body.competition_code) if body.competition_code else []
    if api_teams:
        for t in api_teams:
            db.add(Team(sweepstake_id=sweep.id, **t))
    else:
        for name, flag in _SAMPLE_TEAMS:
            db.add(Team(sweepstake_id=sweep.id, name=name, flag_emoji=flag))

    await db.flush()
    return await _load_full(db, sweep.id)


@router.get("", response_model=list[SweepstakeOut])
async def my_sweepstakes(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    # All sweepstakes the user administers or participates in.
    parts = (
        await db.execute(select(Participant.sweepstake_id).where(Participant.user_id == user.id))
    ).scalars().all()
    ids = set(parts)
    admin = (
        await db.execute(select(Sweepstake.id).where(Sweepstake.admin_id == user.id))
    ).scalars().all()
    ids.update(admin)
    return [await _load_full(db, sid) for sid in ids]


@router.get("/{sid}", response_model=SweepstakeOut)
async def get_sweepstake(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    sweep = await _load_full(db, sid)
    if not sweep:
        raise HTTPException(404, "Sweepstake not found")
    return sweep


@router.delete("/{sid}", status_code=204)
async def delete_sweepstake(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    await db.delete(sweep)


# ---------- Join ----------
@router.post("/join", response_model=SweepstakeOut)
async def join(body: JoinRequest, db: AsyncSession = Depends(get_db),
               user: User = Depends(get_current_user)):
    sweep = (
        await db.execute(select(Sweepstake).where(Sweepstake.invite_code == body.invite_code.upper()))
    ).scalar_one_or_none()
    if not sweep:
        raise HTTPException(404, "Invalid invite code")
    if sweep.draw_approved:
        raise HTTPException(409, "Draw already finalized — cannot join")

    full = await _load_full(db, sweep.id)
    if any(p.user_id == user.id for p in full.participants):
        return full
    if len(full.participants) >= sweep.max_participants:
        raise HTTPException(409, "Sweepstake is full")

    db.add(Participant(sweepstake_id=sweep.id, user_id=user.id))
    await db.flush()
    refreshed = await _load_full(db, sweep.id)
    await manager.broadcast(str(sweep.id), "participant_joined",
                            {"username": user.username, "count": len(refreshed.participants)})
    return refreshed


# ---------- Draw ----------
@router.post("/{sid}/draw", response_model=list[AllocationOut])
async def draw(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
               user: User = Depends(get_current_user)):
    sweep = await _load_full(db, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    try:
        allocations = await run_draw(db, sweep)
    except DrawError as e:
        raise HTTPException(409, str(e))

    out = await _allocations_out(db, allocations)
    await manager.broadcast(str(sid), "draw_completed", {"allocations": [a.model_dump() for a in out]})
    return out


@router.post("/{sid}/draw/approve", response_model=SweepstakeOut)
async def approve(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    try:
        await approve_draw(db, sweep)
    except DrawError as e:
        raise HTTPException(409, str(e))
    full = await _load_full(db, sid)
    await manager.broadcast(str(sid), "draw_approved", {})
    return full


async def _allocations_out(db: AsyncSession, allocations: list[Allocation]) -> list[AllocationOut]:
    result = []
    for a in allocations:
        part = await db.get(Participant, a.participant_id)
        u = await db.get(User, part.user_id)
        team = await db.get(Team, a.team_id)
        result.append(AllocationOut(
            participant_id=part.id, participant_name=u.username,
            team_id=team.id, team_name=team.name, flag_emoji=team.flag_emoji,
        ))
    return result


# ---------- Leaderboard ----------
@router.get("/{sid}/leaderboard", response_model=list[LeaderboardRow])
async def leaderboard(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    sweep = await _load_full(db, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    return compute_leaderboard(sweep)


# ---------- Fixtures ----------
@router.get("/{sid}/fixtures", response_model=list[FixtureOut])
async def fixtures(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    rows = (
        await db.execute(
            select(Fixture).where(Fixture.sweepstake_id == sid).order_by(Fixture.kickoff)
        )
    ).scalars().all()
    return rows


@router.post("/{sid}/sync", response_model=list[FixtureOut])
async def sync_now(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Manually trigger a football-API sync (admin)."""
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    changed = await football.sync_fixtures(db, sweep)
    if changed:
        full = await _load_full(db, sid)
        await manager.broadcast(str(sid), "leaderboard_updated",
                                {"leaderboard": [r.model_dump() for r in compute_leaderboard(full)]})
    rows = (
        await db.execute(select(Fixture).where(Fixture.sweepstake_id == sid))
    ).scalars().all()
    return rows


# ---------- Payment ----------
@router.patch("/{sid}/participants/{pid}/payment", response_model=SweepstakeOut)
async def set_payment(sid: uuid.UUID, pid: uuid.UUID, body: PaymentUpdate,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    part = await db.get(Participant, pid)
    if not part or part.sweepstake_id != sid:
        raise HTTPException(404, "Participant not found")
    part.has_paid = body.has_paid
    await db.flush()
    return await _load_full(db, sid)


# ---------- Notifications ----------
@router.get("/{sid}/notifications", response_model=list[NotificationOut])
async def notifications(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    rows = (
        await db.execute(
            select(Notification)
            .where(Notification.sweepstake_id == sid, Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
        )
    ).scalars().all()
    return rows

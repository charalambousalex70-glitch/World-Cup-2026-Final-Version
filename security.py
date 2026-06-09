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
from app.services.draw import DrawError, approve_draw, reset_draw, run_draw
from app.services.scoring import compute_leaderboard
from app.websocket.manager import manager
from app.models import Fixture

router = APIRouter(prefix="/sweepstakes", tags=["sweepstakes"])

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

    # Default to the World Cup competition on football-data.org ("WC") so the
    # poller can pull live fixtures/results unless the caller specifies another.
    competition = body.competition_code or "WC"

    sweep = Sweepstake(
        name=body.name,
        tournament_name=body.tournament_name,
        competition_code=competition,
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

    # Teams are NOT created here. They're generated at draw time as the top-N
    # ranked contenders (N = number of participants), so the set always matches
    # how many people actually joined. See app.services.draw.run_draw.

    await db.flush()
    return SweepstakeOut.model_validate(await _load_full(db, sweep.id))


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
    # Serialize to Pydantic NOW (session alive, relationships eager-loaded) so
    # nothing lazy-loads during FastAPI's later serialization → no MissingGreenlet.
    out = []
    for sid in ids:
        s = await _load_full(db, sid)
        if s:
            out.append(SweepstakeOut.model_validate(s))
    return out


@router.get("/{sid}", response_model=SweepstakeOut)
async def get_sweepstake(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    sweep = await _load_full(db, sid)
    if not sweep:
        raise HTTPException(404, "Sweepstake not found")
    return SweepstakeOut.model_validate(sweep)


@router.patch("/{sid}", response_model=SweepstakeOut)
async def update_sweepstake(sid: uuid.UUID, body: dict,
                            db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """Admin edits league settings: name, currency, and entry fee (stake)."""
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)

    if "name" in body:
        name = str(body["name"] or "").strip()
        if not name:
            raise HTTPException(422, "League name cannot be empty")
        sweep.name = name
    if "currency" in body and body["currency"] in ("EUR", "GBP", "USD"):
        sweep.currency = body["currency"]
    if "entry_fee" in body:
        try:
            fee = float(body["entry_fee"])
        except (TypeError, ValueError):
            raise HTTPException(422, "Entry fee must be a number")
        if fee < 0:
            raise HTTPException(422, "Entry fee cannot be negative")
        sweep.entry_fee = fee

    await db.flush()
    full = await _load_full(db, sid)
    await manager.broadcast(str(sid), "leaderboard_updated", {})
    return SweepstakeOut.model_validate(full)


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
    code = (body.invite_code or "").strip().upper()
    sweep = (
        await db.execute(select(Sweepstake).where(Sweepstake.invite_code == code))
    ).scalar_one_or_none()
    if not sweep:
        raise HTTPException(404, "Invalid invite code")
    if sweep.draw_approved:
        raise HTTPException(409, "Draw already finalized — cannot join")

    full = await _load_full(db, sweep.id)
    if any(p.user_id == user.id for p in full.participants):
        return SweepstakeOut.model_validate(full)
    if len(full.participants) >= sweep.max_participants:
        raise HTTPException(409, "Sweepstake is full")

    db.add(Participant(sweepstake_id=sweep.id, user_id=user.id))
    await db.flush()
    refreshed = await _load_full(db, sweep.id)
    await manager.broadcast(str(sweep.id), "participant_joined",
                            {"username": user.username, "count": len(refreshed.participants)})
    return SweepstakeOut.model_validate(refreshed)


# ---------- Draw ----------
@router.post("/{sid}/draw", response_model=list[AllocationOut])
async def draw(sid: uuid.UUID, body: dict | None = None,
               db: AsyncSession = Depends(get_db),
               user: User = Depends(get_current_user)):
    sweep = await _load_full(db, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    excluded = (body or {}).get("excluded") or []
    try:
        allocations = await run_draw(db, sweep, excluded=excluded)
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
    return SweepstakeOut.model_validate(full)


@router.post("/{sid}/draw/reset", response_model=SweepstakeOut)
async def reset(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Undo the draw so the admin can re-run it (after more people join, etc.)."""
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    await reset_draw(db, sweep)
    full = await _load_full(db, sid)
    await manager.broadcast(str(sid), "draw_reset", {})
    return SweepstakeOut.model_validate(full)


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
    return SweepstakeOut.model_validate(await _load_full(db, sid))


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

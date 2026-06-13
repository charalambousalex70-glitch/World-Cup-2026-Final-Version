"""Sweepstake routes — the core of the API."""
import secrets
import string
import uuid

import httpx

from app.core.config import settings

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import (
    Allocation, Comment, Notification, Participant, Prediction, PrizeTier, Sweepstake, Team, User,
)
from app.schemas import (
    AllocationOut, CommentCreate, CommentOut, FixtureOut, JoinRequest,
    LeaderboardRow, NotificationOut, PaymentUpdate, PredBoardRow, PredictionIn, PredictionOut, SweepstakeCreate, SweepstakeOut,
)
from app.services import football
from app.services.draw import DrawError, approve_draw, reset_draw, run_draw
from app.services.teams_data import RANKED_TEAMS
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
    if "max_participants" in body:
        try:
            cap = int(body["max_participants"])
        except (TypeError, ValueError):
            raise HTTPException(422, "Max participants must be a whole number")
        # Count current participants so we never set the cap below them.
        current = (
            await db.execute(
                select(func.count())
                .select_from(Participant)
                .where(Participant.sweepstake_id == sid)
            )
        ).scalar() or 0
        if cap < current:
            raise HTTPException(
                422, f"Can't set the limit below the {current} people already in the league."
            )
        if cap > len(RANKED_TEAMS):
            raise HTTPException(
                422, f"Maximum is {len(RANKED_TEAMS)} (one team per person from the ranked list)."
            )
        sweep.max_participants = cap

    await db.flush()
    await db.refresh(sweep)   # ensure we read back the just-saved values
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
    fixtures = (
        await db.execute(select(Fixture).where(Fixture.sweepstake_id == sid))
    ).scalars().all()
    return compute_leaderboard(sweep, fixtures)


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
    rows = (
        await db.execute(select(Fixture).where(Fixture.sweepstake_id == sid))
    ).scalars().all()
    if changed:
        full = await _load_full(db, sid)
        await manager.broadcast(str(sid), "leaderboard_updated",
                                {"leaderboard": [r.model_dump() for r in compute_leaderboard(full, rows)]})
    return rows


# ---------- Participants ----------
@router.delete("/{sid}/participants/{pid}", response_model=SweepstakeOut)
async def remove_participant(sid: uuid.UUID, pid: uuid.UUID,
                             db: AsyncSession = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """Admin removes a participant. Blocked once the draw is approved (teams
    are locked); reset the draw first if you need to remove someone."""
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    _require_admin(sweep, user)
    if sweep.draw_approved:
        raise HTTPException(409, "Draw is locked — reset the draw before removing people.")
    part = await db.get(Participant, pid)
    if not part or part.sweepstake_id != sid:
        raise HTTPException(404, "Participant not found")
    if part.user_id == sweep.admin_id:
        raise HTTPException(422, "The league admin can't remove themselves.")
    # Clear any (unapproved) allocation for them, then remove.
    await db.execute(delete(Allocation).where(Allocation.participant_id == pid))
    await db.delete(part)
    await db.flush()
    full = await _load_full(db, sid)
    await manager.broadcast(str(sid), "participant_joined", {})  # triggers refresh on clients
    return SweepstakeOut.model_validate(full)


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


# ---------- Comments (league chat) ----------
@router.get("/{sid}/comments", response_model=list[CommentOut])
async def list_comments(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    rows = (
        await db.execute(
            select(Comment).where(Comment.sweepstake_id == sid)
            .options(selectinload(Comment.user))
            .order_by(Comment.created_at.desc()).limit(100)
        )
    ).scalars().all()
    import json as _json
    out = []
    for c in reversed(rows):  # oldest first for display
        try: rx = _json.loads(c.reactions) if c.reactions else {}
        except Exception: rx = {}
        out.append(CommentOut(
            id=c.id, body=c.body, created_at=c.created_at,
            username=c.user.username if c.user else "⚽ Goal Bot",
            avatar_color=c.user.avatar_color if c.user else "#ffc83d",
            reactions=rx,
        ))
    return out


@router.post("/{sid}/comments", response_model=CommentOut, status_code=201)
async def post_comment(sid: uuid.UUID, body: CommentCreate,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "Comment cannot be empty")
    c = Comment(sweepstake_id=sid, user_id=user.id, body=text[:500])
    db.add(c)
    # Notify every OTHER participant so they see it in Recent Activity even if
    # they're not online right now.
    parts = (
        await db.execute(select(Participant.user_id).where(Participant.sweepstake_id == sid))
    ).scalars().all()
    excerpt = text[:80] + ("…" if len(text) > 80 else "")
    for uid in parts:
        if uid != user.id:
            db.add(Notification(user_id=uid, sweepstake_id=sid, icon="💬",
                                title=f"{user.username} in {sweep.name}", body=excerpt))
    await db.flush()
    out = CommentOut(
        id=c.id, body=c.body, created_at=c.created_at,
        username=user.username, avatar_color=user.avatar_color,
    )
    await manager.broadcast(str(sid), "comment_added", out.model_dump(mode="json"))
    return out


@router.post("/{sid}/comments/{cid}/react", response_model=CommentOut)
async def react_comment(sid: uuid.UUID, cid: uuid.UUID, body: dict,
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Toggle the current user's emoji reaction on a comment."""
    import json as _json
    emoji = str((body or {}).get("emoji") or "").strip()[:8]
    if not emoji:
        raise HTTPException(422, "Missing emoji")
    c = await db.get(Comment, cid)
    if not c or c.sweepstake_id != sid:
        raise HTTPException(404, "Comment not found")
    try: rx = _json.loads(c.reactions) if c.reactions else {}
    except Exception: rx = {}
    users = set(rx.get(emoji) or [])
    if user.username in users: users.discard(user.username)   # toggle off
    else: users.add(user.username)
    if users: rx[emoji] = sorted(users)
    else: rx.pop(emoji, None)
    c.reactions = _json.dumps(rx)
    await db.flush()
    cu = await db.get(User, c.user_id)
    out = CommentOut(id=c.id, body=c.body, created_at=c.created_at,
                     username=cu.username if cu else "Player",
                     avatar_color=cu.avatar_color if cu else "#888",
                     reactions=rx)
    await manager.broadcast(str(sid), "comment_updated", out.model_dump(mode="json"))
    return out


# ---------- Predictions (side game: 5 exact / 2 result / 0) ----------
def _pred_points(ph, pa, h, a):
    if h is None or a is None: return 0
    if ph == h and pa == a: return 5
    sign = lambda d: (d > 0) - (d < 0)   # 1 home win, 0 draw, -1 away win
    if sign(ph - pa) == sign(h - a): return 2
    return 0


@router.post("/{sid}/predictions", response_model=PredictionOut)
async def save_prediction(sid: uuid.UUID, body: PredictionIn,
                          db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    fx = await db.get(Fixture, body.fixture_id)
    if not fx or fx.sweepstake_id != sid:
        raise HTTPException(404, "Match not found")
    # Lock at kick-off: reject if the match has started/finished OR kickoff has
    # passed, even if the feed hasn't flipped status yet (prevents cheating).
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    started = fx.status != "SCHEDULED" or (fx.kickoff and fx.kickoff <= now)
    if started:
        raise HTTPException(409, "Predictions are locked — the match has kicked off.")
    row = (
        await db.execute(select(Prediction).where(
            Prediction.user_id == user.id, Prediction.fixture_id == body.fixture_id))
    ).scalar_one_or_none()
    if row:
        row.home_pred, row.away_pred = body.home, body.away
    else:
        db.add(Prediction(sweepstake_id=sid, user_id=user.id,
                          fixture_id=body.fixture_id, home_pred=body.home, away_pred=body.away))
    await db.flush()

    # ---- Mirror to the user's OTHER leagues ----
    # Find the same match (same teams + kickoff) in every other league this user
    # is in, and copy the prediction there too — but only where that league's
    # copy of the match hasn't kicked off yet (never overwrite a locked one).
    mirrored = 0
    try:
        my_league_ids = (
            await db.execute(
                select(Participant.sweepstake_id).where(Participant.user_id == user.id)
            )
        ).scalars().all()
        other_ids = [lid for lid in my_league_ids if lid != sid]
        if other_ids:
            twins = (
                await db.execute(
                    select(Fixture).where(
                        Fixture.sweepstake_id.in_(other_ids),
                        Fixture.home_team == fx.home_team,
                        Fixture.away_team == fx.away_team,
                    )
                )
            ).scalars().all()
            for tw in twins:
                # Skip any league copy that's already kicked off / finished.
                if tw.status != "SCHEDULED" or (tw.kickoff and tw.kickoff <= now):
                    continue
                exist = (
                    await db.execute(select(Prediction).where(
                        Prediction.user_id == user.id, Prediction.fixture_id == tw.id))
                ).scalar_one_or_none()
                if exist:
                    exist.home_pred, exist.away_pred = body.home, body.away
                else:
                    db.add(Prediction(sweepstake_id=tw.sweepstake_id, user_id=user.id,
                                      fixture_id=tw.id, home_pred=body.home, away_pred=body.away))
                mirrored += 1
            if mirrored:
                await db.flush()
    except Exception:
        pass  # mirroring is a convenience; never fail the primary save

    return PredictionOut(fixture_id=body.fixture_id, home=body.home, away=body.away,
                         mirrored=mirrored)


@router.get("/{sid}/predictions", response_model=list[PredictionOut])
async def my_predictions(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    rows = (
        await db.execute(select(Prediction).where(
            Prediction.sweepstake_id == sid, Prediction.user_id == user.id))
    ).scalars().all()
    return [PredictionOut(fixture_id=r.fixture_id, home=r.home_pred, away=r.away_pred) for r in rows]


@router.get("/{sid}/predictions/board", response_model=list[PredBoardRow])
async def predictions_board(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
    preds = (
        await db.execute(select(Prediction).where(Prediction.sweepstake_id == sid))
    ).scalars().all()
    fixtures = {f.id: f for f in (
        await db.execute(select(Fixture).where(Fixture.sweepstake_id == sid))
    ).scalars().all()}
    users = {u.id: u for u in (
        await db.execute(select(User).join(Participant, Participant.user_id == User.id)
                         .where(Participant.sweepstake_id == sid))
    ).scalars().all()}
    agg: dict = {}
    for pr in preds:
        fx = fixtures.get(pr.fixture_id)
        if not fx or fx.status != "FINISHED": continue
        pts = _pred_points(pr.home_pred, pr.away_pred, fx.home_score, fx.away_score)
        a = agg.setdefault(pr.user_id, {"points": 0, "exact": 0, "results": 0})
        a["points"] += pts
        if pts == 5: a["exact"] += 1
        elif pts == 2: a["results"] += 1
    out = []
    for uid, u in users.items():
        a = agg.get(uid, {"points": 0, "exact": 0, "results": 0})
        out.append(PredBoardRow(username=u.username, avatar_color=u.avatar_color, **a))
    out.sort(key=lambda r: (-r.points, -r.exact, r.username.lower()))
    return out


@router.get("/{sid}/draw/audit")
async def draw_audit(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Public (league-member) audit of the draw: seed + derivation + result.
    Anyone can recompute SHA256(seed:team)/(seed:participant_id) orderings and
    confirm the published result matches — provably fair."""
    import json as _json
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    if not sweep.draw_audit:
        raise HTTPException(404, "No draw audit yet — audits exist for draws run after the fairness update.")
    audit = _json.loads(sweep.draw_audit)
    # Attach usernames for readability.
    parts = {str(p.id): p for p in (
        await db.execute(select(Participant).options(selectinload(Participant.user))
                         .where(Participant.sweepstake_id == sid))
    ).scalars().all()}
    for row in audit.get("result", []):
        pr = parts.get(row["participant_id"])
        row["username"] = pr.user.username if pr and pr.user else "?"
    return audit


@router.get("/{sid}/standings")
async def get_standings(sid: uuid.UUID, refresh: bool = False,
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Group standings. Serves the cached copy; if empty (or ?refresh=1),
    fetches live from the feed right now so the table isn't blocked on the
    background poller."""
    import json as _json
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    cached = {}
    try:
        cached = _json.loads(sweep.standings) if sweep.standings else {}
    except Exception:
        cached = {}
    if refresh or not cached:
        live = await football.fetch_standings(sweep.competition_code)
        if live:
            sweep.standings = _json.dumps(live)
            await db.flush()
            return live
    return cached


@router.get("/{sid}/fixtures/{fid}/predictions")
async def fixture_predictions(sid: uuid.UUID, fid: uuid.UUID,
                              db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)):
    """Everyone's predictions for one match, with points if it's finished.
    Hidden until kick-off so it can't be used to copy others' picks."""
    from datetime import datetime, timezone
    fx = await db.get(Fixture, fid)
    if not fx or fx.sweepstake_id != sid:
        raise HTTPException(404, "Match not found")
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(select(Prediction).where(Prediction.fixture_id == fid))
    ).scalars().all()
    # Reveal once the match is live/finished OR kick-off time has passed (a
    # time-based fallback so predictions still reveal even if the feed is slow
    # to flip the status). Guard against tz-naive kickoff values from the DB.
    ko = fx.kickoff
    if ko is not None and ko.tzinfo is None:
        ko = ko.replace(tzinfo=timezone.utc)
    started = (fx.status != "SCHEDULED") or (ko is not None and ko <= now)
    # Before kick-off: don't reveal picks (would let people copy), but DO show
    # how many have predicted so far, and whether YOU have.
    if not started:
        mine = next((p for p in rows if p.user_id == user.id), None)
        return {"locked": True, "count": len(rows), "predictions": [],
                "you": {"home": mine.home_pred, "away": mine.away_pred} if mine else None}
    users = {u.id: u for u in (
        await db.execute(select(User)).scalars().all()
    )}
    finished = fx.status == "FINISHED"
    out = []
    for pr in rows:
        try:
            u = users.get(pr.user_id)
            pts = _pred_points(pr.home_pred, pr.away_pred, fx.home_score, fx.away_score) if finished else None
            out.append({
                "username": (u.username if u else "Player"),
                "avatar_color": (u.avatar_color if (u and u.avatar_color) else "#888"),
                "home": pr.home_pred, "away": pr.away_pred, "points": pts,
                "is_me": pr.user_id == user.id,
            })
        except Exception:
            # Never let a single malformed prediction crash the whole response.
            continue
    # Finished: best predictions first. Pre-finish: name order.
    if finished:
        out.sort(key=lambda r: (-(r["points"] or 0), (r["username"] or "").lower()))
    else:
        out.sort(key=lambda r: (r["username"] or "").lower())
    return {"locked": False, "finished": finished,
            "live": fx.status == "LIVE",
            "home_team": fx.home_team, "away_team": fx.away_team,
            "score": [fx.home_score, fx.away_score], "predictions": out}


@router.get("/{sid}/standings/debug")
async def standings_debug(sid: uuid.UUID, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """Diagnostic: shows the competition code, raw feed status, and what the
    standings parser produced — so we can see WHY a table is or isn't showing."""
    import httpx
    from app.core.config import settings
    from app.services import football
    sweep = await db.get(Sweepstake, sid)
    if not sweep:
        raise HTTPException(404, "Not found")
    info = {"competition_code": sweep.competition_code,
            "api_key_configured": bool(getattr(settings, "FOOTBALL_API_KEY", None))}
    # Masked fingerprint of the key actually in use, so it can be compared
    # against the token shown in the football-data.org account without exposing it.
    _k = getattr(settings, "FOOTBALL_API_KEY", "") or ""
    info["api_key_fingerprint"] = (f"{_k[:4]}…{_k[-4:]} (len {len(_k)})" if _k else "MISSING")
    url = f"{settings.FOOTBALL_API_URL}/competitions/{sweep.competition_code}/standings"
    info["url"] = url
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers=football._headers())
            info["http_status"] = r.status_code
            try:
                data = r.json()
                info["standings_blocks"] = len(data.get("standings", []))
                info["block_types"] = [s.get("type") for s in data.get("standings", [])][:8]
                info["block_groups"] = [s.get("group") for s in data.get("standings", [])][:8]
                info["error_message"] = data.get("message")
            except Exception as e:
                info["json_error"] = str(e)[:200]
                info["body_snippet"] = r.text[:300]
    except Exception as e:
        info["request_error"] = str(e)[:200]
    parsed = await football.fetch_standings(sweep.competition_code)
    info["parsed_groups"] = list(parsed.keys())
    info["parsed_total_rows"] = sum(len(v) for v in parsed.values())

    # ALSO probe the matches endpoint — this is the one that drives live scores.
    # The standings 403 may be plan-specific; what matters for live updates is
    # whether /matches returns data and with what status.
    murl = f"{settings.FOOTBALL_API_URL}/competitions/{sweep.competition_code}/matches"
    info["matches_url"] = murl
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            mr = await client.get(murl, headers=football._headers())
            info["matches_http_status"] = mr.status_code
            try:
                mdata = mr.json()
                ms = mdata.get("matches", [])
                info["matches_count"] = len(ms)
                # status breakdown straight from the feed (not our DB)
                from collections import Counter
                info["matches_status_breakdown"] = dict(Counter(m.get("status") for m in ms))
                info["matches_error_message"] = mdata.get("message")
            except Exception as e:
                info["matches_json_error"] = str(e)[:200]
                info["matches_body_snippet"] = mr.text[:300]
    except Exception as e:
        info["matches_request_error"] = str(e)[:200]
    return info

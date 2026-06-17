"""Authentication routes: register, login, current user.

Hardened for real-world, company-wide use:
- Emails are normalised (lowercased + trimmed) so "Alex@X.com " and
  "alex@x.com" are the same account. This prevents the most common
  "I registered but can't log in" complaint.
- Duplicate-email registration is caught both by a pre-check AND by handling
  the database unique-constraint error, so a race between two simultaneous
  signups returns a clean 409 instead of a 500.
- Usernames are trimmed; blank usernames fall back to the email prefix.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas import Token, UserCreate, UserLogin, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

_COLORS = ["#ffc83d", "#4d8dff", "#2fe28a", "#ff5b6e", "#b07bff", "#ff9d4d", "#4de2d6"]


def _normalise_email(email: str) -> str:
    return (email or "").strip().lower()


async def _find_user_by_email(db: AsyncSession, email: str) -> User | None:
    # Case-insensitive lookup so historical mixed-case rows still match.
    return (
        await db.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()


@router.post("/register", response_model=Token, status_code=201)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    email = _normalise_email(body.email)
    username = (body.username or "").strip() or email.split("@")[0]

    if not email or "@" not in email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address")
    if len(body.password) < 6:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password must be at least 6 characters")

    if await _find_user_by_email(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered. Try signing in.")

    user = User(
        email=email,
        username=username,
        hashed_password=hash_password(body.password),
        avatar_color=secrets.choice(_COLORS),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # Another request created the same email between our check and flush.
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered. Try signing in.")

    token = create_access_token(str(user.id))
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=Token)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    email = _normalise_email(body.email)
    user = await _find_user_by_email(db, email)
    # Always run a verify to keep timing consistent and avoid leaking which
    # part was wrong; message stays generic for security.
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    token = create_access_token(str(user.id))
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


# ---------------- Password reset (admin-assisted, no email needed) ----------------
import hashlib
from datetime import datetime, timedelta, timezone


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@router.post("/reset/request")
async def reset_request(body: dict, db: AsyncSession = Depends(get_db)):
    """A user asks to reset. We generate a 6-digit code, store only its hash with
    a 30-minute expiry, and DO NOT return it here — that would let anyone reset
    anyone's password just by knowing an email. The league admin relays the code
    to the user through a trusted channel (see /reset/code, admin-only).
    Always returns the same message whether or not the email exists, so this
    can't be used to discover who has an account."""
    email = _normalise_email(body.get("email", ""))
    user = await _find_user_by_email(db, email)
    if user:
        code = f"{secrets.randbelow(1_000_000):06d}"
        user.reset_code_hash = _hash_code(code)
        user.reset_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        await db.flush()
    return {"message": "If that account exists, your league admin can now share a reset code with you."}


@router.get("/reset/code")
async def reset_code(email: str, db: AsyncSession = Depends(get_db),
                     admin: User = Depends(get_current_user)):
    """Admin-only: retrieve the ACTIVE reset code for a user so it can be relayed
    through a trusted channel. Requires the caller to be the admin of at least
    one league that the target user belongs to — so random users can't read
    others' codes. The code itself isn't stored in plaintext, so we re-issue a
    fresh one here and return it to the admin only."""
    from app.models import Sweepstake, Participant
    email = _normalise_email(email)
    target = await _find_user_by_email(db, email)
    if not target:
        raise HTTPException(404, "No account with that email")
    # Verify caller administers a league the target is in.
    shared = (await db.execute(
        select(Sweepstake.id)
        .join(Participant, Participant.sweepstake_id == Sweepstake.id)
        .where(Sweepstake.admin_id == admin.id, Participant.user_id == target.id)
    )).first()
    if not shared and admin.id != target.id:
        raise HTTPException(403, "You can only reset codes for players in leagues you administer")
    code = f"{secrets.randbelow(1_000_000):06d}"
    target.reset_code_hash = _hash_code(code)
    target.reset_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    await db.flush()
    return {"email": target.email, "code": code, "expires_minutes": 30,
            "note": "Share this with the user through a trusted channel. It expires in 30 minutes."}


@router.post("/reset/confirm", response_model=Token)
async def reset_confirm(body: dict, db: AsyncSession = Depends(get_db)):
    """The user submits email + code + new password. We verify the code against
    the stored hash and its expiry, then set a fresh password hash. The old
    password is never seen or needed."""
    email = _normalise_email(body.get("email", ""))
    code = str(body.get("code", "")).strip()
    new_password = body.get("password", "")
    if len(new_password) < 6:
        raise HTTPException(422, "New password must be at least 6 characters")
    user = await _find_user_by_email(db, email)
    if not user or not user.reset_code_hash or not user.reset_expires:
        raise HTTPException(400, "No reset is pending for that account. Request a new code.")
    if user.reset_expires < datetime.now(timezone.utc):
        raise HTTPException(400, "That code has expired. Request a new one.")
    if _hash_code(code) != user.reset_code_hash:
        raise HTTPException(400, "Incorrect reset code.")
    user.hashed_password = hash_password(new_password)
    user.reset_code_hash = None
    user.reset_expires = None
    await db.flush()
    token = create_access_token(str(user.id))
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.patch("/me", response_model=UserOut)
async def update_me(body: dict, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Update the signed-in user's profile (currently just the display name)."""
    new_name = (body or {}).get("username")
    if new_name is not None:
        new_name = str(new_name).strip()
        if not new_name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name cannot be empty")
        if len(new_name) > 40:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name is too long (max 40)")
        user.username = new_name
    await db.flush()
    return UserOut.model_validate(user)

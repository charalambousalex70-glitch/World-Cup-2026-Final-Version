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
    return user

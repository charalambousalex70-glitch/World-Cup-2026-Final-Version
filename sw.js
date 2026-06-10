"""Password hashing and JWT creation/verification.

Uses the `bcrypt` library directly rather than the `passlib` wrapper, which
breaks against modern bcrypt releases (the classic
`module 'bcrypt' has no attribute '__about__'` warning followed by a
ValueError). Bcrypt only hashes the first 72 bytes of a password, so we
truncate to 72 bytes explicitly to avoid the "password cannot be longer than
72 bytes" crash on long inputs.
"""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# Bcrypt's hard limit. Longer inputs must be truncated before hashing.
_MAX_BCRYPT_BYTES = 72


def _prepare(password: str) -> bytes:
    """Encode and safely truncate to bcrypt's 72-byte maximum."""
    return password.encode("utf-8")[:_MAX_BCRYPT_BYTES]


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(_prepare(password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str) -> str:
    """subject is the user id (stringified)."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> str | None:
    """Returns the subject (user id) or None if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

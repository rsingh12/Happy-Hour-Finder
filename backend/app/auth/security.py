"""
Password hashing (bcrypt) and JWT issuance/verification.

Uses bcrypt directly rather than passlib, because passlib 1.7.4 is
unmaintained and fails on bcrypt >= 5.x with spurious 72-byte errors.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.config import settings


# bcrypt has a hard 72-byte limit on the password input. Most apps just
# truncate; some hash with sha256 first to allow arbitrary length. We
# truncate, which is the standard approach.
_BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    plain_bytes = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(plain_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    plain_bytes = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(plain_bytes, hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    user_id: str,
    *,
    extra_claims: Optional[dict] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=settings.jwt_expire_days))
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Raises jose.JWTError on invalid or expired token."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])

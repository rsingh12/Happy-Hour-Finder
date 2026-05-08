"""
Auth routes: register, verify email, login, /me.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.auth.email import send_verification_code
from app.auth.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models import EmailVerificationCode, User


router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- Pydantic schemas ----------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=32)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class VerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email_verified: bool


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str]
    phone: Optional[str]
    email_verified: bool


# ---------- Helpers ----------

def _generate_code() -> str:
    """Generate a 6-digit verification code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _issue_verification_code(db: Session, user: User) -> str:
    """Create a new verification code, invalidating older ones."""
    db.query(EmailVerificationCode).filter(
        EmailVerificationCode.user_id == user.id,
        EmailVerificationCode.used.is_(False),
    ).update({"used": True})

    code = _generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    record = EmailVerificationCode(
        user_id=user.id,
        code=code,
        expires_at=expires_at,
    )
    db.add(record)
    db.flush()
    return code


# ---------- Routes ----------

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email.lower()).one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        phone=payload.phone,
        email_verified=False,
    )
    db.add(user)
    db.flush()

    code = _issue_verification_code(db, user)

    # Promote any pending friend records (where someone else added this
    # user by email/phone before they signed up) to active.
    from app.friends.service import reconcile_pending_for_new_user
    reconciled = reconcile_pending_for_new_user(db, user)
    if reconciled:
        print(f"[auth] Reconciled {reconciled} pending friend record(s) for {user.email}")

    db.commit()
    db.refresh(user)

    send_verification_code(user.email, code)

    token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=token,
        user_id=str(user.id),
        email_verified=user.email_verified,
    )


@router.post("/verify", response_model=UserResponse)
def verify_email(payload: VerifyRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    record = (
        db.query(EmailVerificationCode)
        .filter(
            EmailVerificationCode.user_id == user.id,
            EmailVerificationCode.code == payload.code,
            EmailVerificationCode.used.is_(False),
        )
        .order_by(EmailVerificationCode.created_at.desc())
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid code",
        )

    now = datetime.now(timezone.utc)
    if record.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code expired",
        )

    record.used = True
    user.email_verified = True
    db.commit()
    db.refresh(user)

    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        phone=user.phone,
        email_verified=user.email_verified,
    )


@router.post("/resend-code", response_model=dict)
def resend_code(email: EmailStr, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email.lower()).one_or_none()
    if not user:
        # Don't leak account existence
        return {"sent": True}

    if user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already verified",
        )

    code = _issue_verification_code(db, user)
    db.commit()
    send_verification_code(user.email, code)
    return {"sent": True}


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=token,
        user_id=str(user.id),
        email_verified=user.email_verified,
    )


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        phone=user.phone,
        email_verified=user.email_verified,
    )

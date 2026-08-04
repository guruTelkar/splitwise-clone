"""OTP verification, forgot-password, forgot-user-id endpoints."""

import random
import string
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import OtpCode, User
from ..schemas import (
    ForgotPasswordRequest,
    ForgotUserIdRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendOtpRequest,
    VerifyOtpRequest,
)
from ..security import create_access_token, hash_password, is_placeholder, verify_password
from ..service import serialize_user

router = APIRouter(prefix="/auth", tags=["otp"])

# In-memory OTP store (for demo: log OTP to console; production would use SMTP/SMS)
_otp_store: dict[str, dict] = {}


def _generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def _store_otp(db: Session, email: str | None, mobile: str | None, purpose: str) -> str:
    code = _generate_code()
    otp = OtpCode(
        email=email.lower().strip() if email else None,
        mobile=mobile.strip() if mobile else None,
        code=code,
        purpose=purpose,
    )
    db.add(otp)
    db.commit()
    # For demo, log the OTP (production would send via SMTP/SMS)
    target = email or mobile
    print(f"[OTP] {purpose} -> {target}: {code}")
    return code


def _verify_otp(
    db: Session, email: str | None, mobile: str | None, code: str, purpose: str
) -> bool:
    q = db.query(OtpCode).filter(
        OtpCode.purpose == purpose,
        OtpCode.used == False,
    )
    if email:
        q = q.filter(OtpCode.email == email.lower().strip())
    elif mobile:
        q = q.filter(OtpCode.mobile == mobile.strip())
    else:
        return False
    otp = q.order_by(OtpCode.id.desc()).first()
    if otp is None:
        return False
    # Allow 10 minutes
    age = datetime.now(timezone.utc) - otp.created_at.replace(tzinfo=timezone.utc)
    if age > timedelta(minutes=10):
        return False
    if otp.code != code:
        return False
    otp.used = True
    db.commit()
    return True


@router.post("/send-otp")
def send_otp(payload: SendOtpRequest, db: Session = Depends(get_db)):
    if not payload.email and not payload.mobile:
        raise HTTPException(status_code=400, detail="Provide email or mobile")
    target = payload.email or payload.mobile
    code = _store_otp(db, payload.email, payload.mobile, payload.purpose)
    return {"message": f"OTP sent to {target}", "hint": code}


@router.post("/verify-otp")
def verify_otp(payload: VerifyOtpRequest, db: Session = Depends(get_db)):
    if not payload.email and not payload.mobile:
        raise HTTPException(status_code=400, detail="Provide email or mobile")
    ok = _verify_otp(db, payload.email, payload.mobile, payload.code, payload.purpose)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    return {"verified": True}


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    if not payload.email and not payload.mobile:
        raise HTTPException(status_code=400, detail="Provide email or mobile")
    user = None
    if payload.email:
        user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    elif payload.mobile:
        user = db.query(User).filter(User.mobile == payload.mobile.strip()).first()
    if user is None:
        return {"message": "If an account exists, a reset code has been sent."}
    code = _store_otp(db, user.email, user.mobile, "forgot_password")
    return {"message": "Reset code sent", "hint": code}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    ok = _verify_otp(db, payload.email, None, payload.code, "forgot_password")
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password reset successfully"}


@router.post("/forgot-userid")
def forgot_userid(payload: ForgotUserIdRequest, db: Session = Depends(get_db)):
    if not payload.email and not payload.mobile:
        raise HTTPException(status_code=400, detail="Provide email or mobile")
    user = None
    if payload.email:
        user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    elif payload.mobile:
        user = db.query(User).filter(User.mobile == payload.mobile.strip()).first()
    if user is None:
        return {"message": "If an account exists, user ID has been sent to your contact."}
    code = _store_otp(db, user.email, user.mobile, "forgot_userid")
    return {"message": f"User ID info sent. Your user ID is {user.id}", "user_id": user.id, "hint": code}

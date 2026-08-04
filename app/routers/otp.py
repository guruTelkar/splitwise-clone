"""OTP verification, forgot-password, forgot-user-id endpoints."""

import random
import string
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import OtpCode, User
from ..notifications import send_otp_email, send_otp_sms
from ..schemas import (
    ForgotPasswordRequest,
    ForgotUserIdRequest,
    ResetPasswordRequest,
    SendOtpRequest,
    VerifyOtpRequest,
)
from ..security import hash_password, verify_password
from ..service import serialize_user

router = APIRouter(prefix="/auth", tags=["otp"])

RESEND_COOLDOWN_SECONDS = 30
OTP_EXPIRY_MINUTES = 10


def _generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def _store_otp(db: Session, email: str | None, mobile: str | None, purpose: str) -> tuple[str, bool]:
    """Store an OTP and attempt delivery. Returns (code, email_delivered)."""
    code = _generate_code()
    otp = OtpCode(
        email=email.lower().strip() if email else None,
        mobile=mobile.strip() if mobile else None,
        code=code,
        purpose=purpose,
    )
    db.add(otp)
    db.commit()
    # Send OTP via email and/or SMS
    delivered = False
    if email:
        delivered = send_otp_email(email, code, purpose)
    if mobile:
        send_otp_sms(mobile, code, purpose)
    return code, delivered


def _check_cooldown(db: Session, email: str | None, mobile: str | None, purpose: str) -> float:
    """Return seconds remaining until cooldown expires. 0 means OK to send."""
    q = db.query(OtpCode).filter(OtpCode.purpose == purpose)
    if email:
        q = q.filter(OtpCode.email == email.lower().strip())
    elif mobile:
        q = q.filter(OtpCode.mobile == mobile.strip())
    else:
        return 0
    last = q.order_by(OtpCode.id.desc()).first()
    if last is None:
        return 0
    elapsed = (datetime.now(timezone.utc) - last.created_at.replace(tzinfo=timezone.utc)).total_seconds()
    remaining = RESEND_COOLDOWN_SECONDS - elapsed
    return max(0.0, remaining)


def verify_otp_code(
    db: Session, email: str | None, mobile: str | None, code: str, purpose: str
) -> bool:
    """Verify an OTP code. Returns True if valid and not expired."""
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
    age = datetime.now(timezone.utc) - otp.created_at.replace(tzinfo=timezone.utc)
    if age > timedelta(minutes=OTP_EXPIRY_MINUTES):
        return False
    if otp.code != code:
        return False
    otp.used = True
    db.commit()
    return True


def is_recently_verified(db: Session, email: str, purpose: str) -> bool:
    """Check if there's a recently used (verified) OTP for this email+purpose within expiry window."""
    q = db.query(OtpCode).filter(
        OtpCode.purpose == purpose,
        OtpCode.used == True,
        OtpCode.email == email.lower().strip(),
    )
    otp = q.order_by(OtpCode.id.desc()).first()
    if otp is None:
        return False
    age = datetime.now(timezone.utc) - otp.created_at.replace(tzinfo=timezone.utc)
    return age <= timedelta(minutes=OTP_EXPIRY_MINUTES)


def is_recently_verified_mobile(db: Session, mobile: str, purpose: str) -> bool:
    """Check if there's a recently used (verified) OTP for this mobile+purpose within expiry window."""
    q = db.query(OtpCode).filter(
        OtpCode.purpose == purpose,
        OtpCode.used == True,
        OtpCode.mobile == mobile.strip(),
    )
    otp = q.order_by(OtpCode.id.desc()).first()
    if otp is None:
        return False
    age = datetime.now(timezone.utc) - otp.created_at.replace(tzinfo=timezone.utc)
    return age <= timedelta(minutes=OTP_EXPIRY_MINUTES)


@router.post("/send-otp")
def send_otp(payload: SendOtpRequest, db: Session = Depends(get_db)):
    if not payload.email and not payload.mobile:
        raise HTTPException(status_code=400, detail="Provide email or mobile")
    target = payload.email or payload.mobile
    cooldown = _check_cooldown(db, payload.email, payload.mobile, payload.purpose)
    if cooldown > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {int(cooldown)} seconds before requesting another OTP",
        )
    code, delivered = _store_otp(db, payload.email, payload.mobile, payload.purpose)
    return {"message": f"OTP sent to {target}", "hint": code, "email_delivered": delivered}


@router.post("/verify-otp")
def verify_otp(payload: VerifyOtpRequest, db: Session = Depends(get_db)):
    if not payload.email and not payload.mobile:
        raise HTTPException(status_code=400, detail="Provide email or mobile")
    if not payload.code or not payload.code.strip():
        raise HTTPException(status_code=400, detail="OTP code is required")
    ok = verify_otp_code(db, payload.email, payload.mobile, payload.code.strip(), payload.purpose)
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
    code, delivered = _store_otp(db, user.email, user.mobile, "forgot_password")
    return {"message": "Reset code sent", "hint": code, "email_delivered": delivered}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    ok = verify_otp_code(db, payload.email, None, payload.code, "forgot_password")
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
    code, delivered = _store_otp(db, user.email, user.mobile, "forgot_userid")
    return {"message": f"User ID info sent. Your user ID is {user.id}", "user_id": user.id, "hint": code, "email_delivered": delivered}

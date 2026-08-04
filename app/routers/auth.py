from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import LoginRequest, RegisterRequest, TokenResponse
from ..security import (
    create_access_token,
    hash_password,
    is_placeholder,
    verify_password,
)
from ..service import serialize_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    mobile = payload.mobile.strip()

    # Enforce OTP verification: both email and mobile must be verified before account creation
    from .otp import is_recently_verified, is_recently_verified_mobile
    if not is_recently_verified(db, email, "register_email"):
        raise HTTPException(
            status_code=400,
            detail="Email not verified. Please verify your email with the OTP code before creating an account.",
        )
    if not is_recently_verified_mobile(db, mobile, "register_mobile"):
        raise HTTPException(
            status_code=400,
            detail="Mobile not verified. Please verify your mobile number with the OTP code before creating an account.",
        )

    user = db.query(User).filter(User.email == email).first()
    if user is not None:
        if not is_placeholder(user.password_hash):
            raise HTTPException(status_code=400, detail="Email already registered")
        # Claim a lightweight account that was created by adding a friend by email.
        user.name = payload.name.strip()
        user.password_hash = hash_password(payload.password)
        if payload.mobile:
            user.mobile = payload.mobile.strip()
        db.commit()
        db.refresh(user)
        return TokenResponse(token=create_access_token(user.id), user=serialize_user(user))
    user = User(
        email=email,
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        mobile=mobile,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(token=create_access_token(user.id), user=serialize_user(user))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return TokenResponse(token=create_access_token(user.id), user=serialize_user(user))

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

# Sentinel hash for lightweight accounts created by adding a friend by email.
# Never a valid bcrypt hash, so these accounts cannot be logged into until
# they register and "claim" the email address.
PLACEHOLDER_PASSWORD = "!splitwise-placeholder"


def is_placeholder(user_hash: str) -> bool:
    return user_hash == PLACEHOLDER_PASSWORD


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return int(payload["sub"])
    except Exception:
        return None

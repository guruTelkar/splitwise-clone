"""Seed a demo user so the app can log in immediately against a fresh database.

Usage:
    python seed.py                          # creates tester@example.com / password123yy
    python seed.py --email you@x.com --name You --password secret123
"""

import argparse

from app.database import SessionLocal
from app.models import User
from app.security import hash_password


def seed(email: str, name: str, password: str) -> None:
    db = SessionLocal()
    try:
        email = email.lower().strip()
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"User {email} already exists (id={existing.id})")
            return
        user = User(email=email, name=name.strip(), password_hash=hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Created user {email} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="tester@example.com")
    parser.add_argument("--name", default="Tester")
    parser.add_argument("--password", default="password123yy")
    args = parser.parse_args()
    seed(args.email, args.name, args.password)

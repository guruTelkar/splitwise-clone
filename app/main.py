from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import settings
from .database import Base, engine
from .routers import (
    activity,
    auth,
    currency,
    expenses,
    friends,
    groups,
    notifications,
    otp,
    packing,
    payments,
    receipts,
    stats,
    users,
)


def _run_startup_migrations():
    """Add columns that may not exist yet (handles both SQLite and PostgreSQL)."""
    with engine.connect() as conn:
        dialect = conn.dialect.name
        try:
            if dialect == "sqlite":
                cols = {r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
                if "mobile" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN mobile VARCHAR(20)"))
                    conn.commit()
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS mobile VARCHAR(20)"))
                conn.commit()
        except Exception:
            pass
        try:
            if dialect == "sqlite":
                cols = {r[1] for r in conn.execute(text("PRAGMA table_info(expenses)")).fetchall()}
                if "location" not in cols:
                    conn.execute(text("ALTER TABLE expenses ADD COLUMN location VARCHAR(500)"))
                    conn.commit()
            else:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN IF NOT EXISTS location VARCHAR(500)"))
                conn.commit()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _run_startup_migrations()
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(settings.upload_dir)), name="uploads")

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(otp.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(friends.router, prefix=settings.api_prefix)
app.include_router(groups.router, prefix=settings.api_prefix)
app.include_router(expenses.router, prefix=settings.api_prefix)
app.include_router(expenses.recurring_router, prefix=settings.api_prefix)
app.include_router(payments.router, prefix=settings.api_prefix)
app.include_router(activity.router, prefix=settings.api_prefix)
app.include_router(currency.router, prefix=settings.api_prefix)
app.include_router(receipts.router, prefix=settings.api_prefix)
app.include_router(stats.router, prefix=settings.api_prefix)
app.include_router(packing.router, prefix=settings.api_prefix)
app.include_router(notifications.router, prefix=settings.api_prefix)
app.include_router(notifications.reminder_router, prefix=settings.api_prefix)


@app.get("/")
def root():
    return {"name": settings.app_name, "docs": "/docs", "api": settings.api_prefix}

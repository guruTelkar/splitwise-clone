from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    app_name: str = "Splitwise Clone"
    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{(DATA_DIR / 'splitwise.db').as_posix()}"
    jwt_secret: str = "change-me-in-production-please"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 days
    upload_dir: Path = DATA_DIR / "uploads"
    cors_origins: list[str] = ["*"]

    # Email provider (Resend - free: 100/day, 3000/mo)
    resend_api_key: str = ""
    email_from: str = "Splitwise Clone <onboarding@resend.dev>"

    # SMS provider (Twilio - free trial: $15 credit)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    class Config:
        env_file = str(BASE_DIR / ".env")


settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)

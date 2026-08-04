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

    # Email provider (Gmail REST API over HTTPS - works on Render free tier,
    # unlike SMTP ports 25/465/587 which free instances block)
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_user: str = "splititup.support@gmail.com"
    email_from: str = "Splitwise Clone <splititup.support@gmail.com>"

    # SMTP fallback (works only on paid Render instances)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    class Config:
        env_file = str(BASE_DIR / ".env")


settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)

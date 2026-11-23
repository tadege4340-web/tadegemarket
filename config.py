
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL


# Always load the .env file located beside this config.py.
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)


class Config:
    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "change-this-secret-before-deployment"
    )

    # Aiven PostgreSQL configuration
    DB_HOST = os.getenv("DB_HOST", "").strip()
    DB_PORT = int(os.getenv("DB_PORT", "15670"))
    DB_USER = os.getenv("DB_USER", "").strip()
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "defaultdb").strip()
    DB_SSLMODE = os.getenv("DB_SSLMODE", "require").strip()

    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise RuntimeError(
            "Database configuration is incomplete. "
            "Check DB_HOST, DB_USER, DB_PASSWORD and DB_NAME in .env."
        )

    SQLALCHEMY_DATABASE_URI = URL.create(
        drivername="postgresql+psycopg",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        query={
            "sslmode": DB_SSLMODE
        }
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_timeout": 30,
        "connect_args": {
            "connect_timeout": 20
        }
    }

    MAX_CONTENT_LENGTH = 5 * 1024 * 1024

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = (
        os.getenv("COOKIE_SECURE", "false").lower() == "true"
    )

# src/clashdb/db.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _load_env() -> None:
    """
    Load .env from repo root if present.
    Safe to call multiple times.
    """
    # repo root = .../src/clashdb/db.py -> parents[2]
    root = Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def get_database_url() -> str:
    """
    Prefer DATABASE_URL. Otherwise build from POSTGRES_* vars.

    Works with your docker-compose defaults as long as .env has:
      POSTGRES_HOST=localhost
      POSTGRES_PORT=5432
      POSTGRES_DB=...
      POSTGRES_USER=...
      POSTGRES_PASSWORD=...
    """
    _load_env()

    url = os.getenv("DATABASE_URL")
    if url:
        return url

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "postgres")
    user = os.getenv("POSTGRES_USER", "postgres")
    pw = os.getenv("POSTGRES_PASSWORD", "postgres")

    # SQLAlchemy URL
    return f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"


_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    """
    Singleton SQLAlchemy engine for the project.
    """
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(get_database_url(), future=True)
    return _ENGINE

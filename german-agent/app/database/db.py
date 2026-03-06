import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker, DeclarativeBase

load_dotenv()

# 4 slashes = absolute path inside the container: sqlite:////app/data/german.db
# 3 slashes = relative path from CWD, which would resolve incorrectly inside Docker
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/data/german.db")

# For SQLite, ensure the parent directory exists before the engine tries to open the file
if DATABASE_URL.startswith("sqlite"):
    _db_file = make_url(DATABASE_URL).database  # e.g. "/app/data/german.db"
    if _db_file and _db_file != ":memory:":
        Path(_db_file).parent.mkdir(parents=True, exist_ok=True)

# SQLite needs connect_args to work correctly with FastAPI's thread model
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a DB session and ensures it closes after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Called once on app startup."""
    # Import models here so Base.metadata is populated before create_all
    from app.database import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

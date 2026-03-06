import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

load_dotenv()

# 4 slashes = absolute path inside the container: sqlite:////app/data/german.db
# 3 slashes = relative path from CWD — resolves incorrectly inside Docker
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/data/german.db")


def _ensure_sqlite_dir(url: str) -> None:
    """
    Create the parent directory for a SQLite database file if it doesn't exist.

    Parses the file path using simple string ops rather than make_url() to
    avoid any SQLAlchemy version differences in how .database is returned.

    sqlite:////app/data/german.db  →  ensures /app/data/ exists  (absolute)
    sqlite:///data/german.db       →  ensures CWD/data/ exists   (relative)
    """
    if not url.startswith("sqlite:///"):
        return
    # Strip "sqlite:///" → leftover is the raw path
    # "sqlite:////app/data/foo.db" → "/app/data/foo.db"  (absolute, starts with /)
    # "sqlite:///data/foo.db"      → "data/foo.db"        (relative, no leading /)
    raw = url[len("sqlite:///"):]
    if not raw or raw == ":memory:":
        return

    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    data_dir = db_path.parent
    print(f"[db.py] Ensuring SQLite data dir exists: {data_dir}", flush=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"[db.py] Data dir ready: {data_dir.exists()}", flush=True)


_ensure_sqlite_dir(DATABASE_URL)

# SQLite needs check_same_thread=False to work with FastAPI's thread pool
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
    # Belt-and-suspenders: ensure the directory exists here too, in case
    # module-level code ran before the Docker volume was fully mounted.
    _ensure_sqlite_dir(DATABASE_URL)

    # Import models here so Base.metadata is populated before create_all
    from app.database import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

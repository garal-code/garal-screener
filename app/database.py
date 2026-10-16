import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()


def normalize_database_url(url: str) -> str:
    """Pin the PostgreSQL driver to psycopg2, the one in requirements.txt.

    Railway hands out the legacy `postgres://` scheme, and since SQLAlchemy
    2.1 a bare `postgresql://` resolves to psycopg (v3), which is not
    installed. URLs that already name a driver, and non-Postgres URLs, are
    returned unchanged.
    """
    for scheme in ("postgres://", "postgresql://"):
        if url.startswith(scheme):
            return "postgresql+psycopg2://" + url[len(scheme) :]
    return url


DATABASE_URL = normalize_database_url(
    os.getenv("DATABASE_URL", "sqlite:///./cartera.db")
)

# SQLite necesita check_same_thread=False para funcionar con FastAPI
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency de FastAPI: provee una sesión y la cierra al terminar."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crea todas las tablas si no existen. Solo para SQLite local — en
    Postgres el esquema lo gestiona Alembic (`alembic upgrade head`)."""
    if not DATABASE_URL.startswith("sqlite"):
        return

    from app.models import Base  # import local para evitar circular

    Base.metadata.create_all(bind=engine)

import pytest

from app.database import normalize_database_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Railway's legacy scheme
        (
            "postgres://user:pass@host:5432/db",
            "postgresql+psycopg2://user:pass@host:5432/db",
        ),
        # SQLAlchemy 2.1 maps a bare postgresql:// to psycopg (v3), which is
        # not installed; the driver must be pinned to psycopg2.
        (
            "postgresql://user:pass@host:5432/db",
            "postgresql+psycopg2://user:pass@host:5432/db",
        ),
        # An explicit driver is respected
        (
            "postgresql+psycopg2://user:pass@host:5432/db",
            "postgresql+psycopg2://user:pass@host:5432/db",
        ),
        (
            "postgresql+asyncpg://user:pass@host:5432/db",
            "postgresql+asyncpg://user:pass@host:5432/db",
        ),
        # SQLite is left untouched
        ("sqlite:///./cartera.db", "sqlite:///./cartera.db"),
    ],
)
def test_normalize_database_url(url, expected):
    assert normalize_database_url(url) == expected


def test_normalize_only_rewrites_the_scheme():
    url = "postgres://user:postgres://odd@host:5432/postgresql://db"
    assert (
        normalize_database_url(url)
        == "postgresql+psycopg2://user:postgres://odd@host:5432/postgresql://db"
    )


def test_postgres_url_resolves_to_the_installed_psycopg2_driver():
    from sqlalchemy.engine import make_url

    url = make_url(normalize_database_url("postgres://user:pass@host:5432/db"))
    assert url.get_dialect().driver == "psycopg2"

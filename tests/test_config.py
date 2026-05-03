from src.config import Settings, psycopg_dsn


def test_postgres_configured_requires_all_connection_fields() -> None:
    assert not Settings(postgres_host="postgres").postgres_configured
    assert Settings(
        postgres_host="postgres",
        postgres_db="ranger",
        postgres_user="app",
        postgres_password="secret",
    ).postgres_configured
    assert Settings(database_url="postgresql://user:pass@postgres/db").postgres_configured


def test_psycopg_dsn_accepts_pgvector_sqlalchemy_style_url() -> None:
    assert psycopg_dsn("postgresql+psycopg://user:pass@postgres/db") == (
        "postgresql://user:pass@postgres/db"
    )

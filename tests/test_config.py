from src.config import Settings, csv_env, psycopg_dsn


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


def test_csv_env_parses_frontend_origin_allowlist() -> None:
    assert csv_env("http://localhost:3000, https://app.example.test ,,") == (
        "http://localhost:3000",
        "https://app.example.test",
    )


def test_settings_accepts_optional_frontend_boundary_config() -> None:
    config = Settings(
        system1_api_key="dev-key",
        cors_allow_origins=("http://localhost:3000",),
    )

    assert config.system1_api_key == "dev-key"
    assert config.cors_allow_origins == ("http://localhost:3000",)

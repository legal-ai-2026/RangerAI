from src.config import Settings


def test_postgres_configured_requires_all_connection_fields() -> None:
    assert not Settings(postgres_host="postgres").postgres_configured
    assert Settings(
        postgres_host="postgres",
        postgres_db="ranger",
        postgres_user="app",
        postgres_password="secret",
    ).postgres_configured

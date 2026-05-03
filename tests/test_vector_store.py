import pytest

from src.agent.vector_store import PgVectorStore, build_vector_store, vector_literal
from src.config import Settings


def test_build_vector_store_returns_none_without_postgres_config() -> None:
    assert build_vector_store(Settings()) is None


def test_build_vector_store_uses_postgres_config_and_dimensions() -> None:
    store = build_vector_store(
        Settings(
            postgres_host="postgres",
            postgres_db="ranger",
            postgres_user="app",
            postgres_password="secret",
            embedding_dimensions=3,
        )
    )

    assert isinstance(store, PgVectorStore)
    assert store.dimensions == 3


def test_vector_literal_formats_numeric_values_for_pgvector_cast() -> None:
    assert vector_literal([1, 0.25, -2]) == "[1.0,0.25,-2.0]"


def test_vector_literal_rejects_empty_embedding() -> None:
    with pytest.raises(ValueError):
        vector_literal([])


def test_vector_literal_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        vector_literal([float("nan")])


def test_pgvector_store_validates_embedding_dimensions() -> None:
    store = PgVectorStore(
        host="postgres",
        port=5432,
        dbname="ranger",
        user="app",
        password="secret",
        dimensions=3,
    )

    with pytest.raises(ValueError):
        store.search(namespace="doctrine", embedding=[1.0, 2.0], limit=1)


def test_pgvector_store_validates_positive_dimensions_and_limit() -> None:
    with pytest.raises(ValueError):
        PgVectorStore(
            host="postgres",
            port=5432,
            dbname="ranger",
            user="app",
            password="secret",
            dimensions=0,
        )

    store = PgVectorStore(
        host="postgres",
        port=5432,
        dbname="ranger",
        user="app",
        password="secret",
        dimensions=2,
    )
    with pytest.raises(ValueError):
        store.search(namespace="doctrine", embedding=[1.0, 2.0], limit=0)

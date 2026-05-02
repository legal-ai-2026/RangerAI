from src.agent.store import InMemoryRunStore
from src.api import main


def test_healthz_reports_dependency_availability(monkeypatch) -> None:
    previous_store = main.store
    try:
        main.store = InMemoryRunStore()
        monkeypatch.setattr(main.workflow.kg, "health", lambda: True)
        monkeypatch.setattr(main, "redis_health", lambda _url: True)

        response = main.healthz()

        assert response["dependencies_available"] == {
            "run_store": True,
            "redis": True,
            "falkordb": True,
        }
    finally:
        main.store = previous_store

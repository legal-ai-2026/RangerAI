from src.agent.doctrine import lookup_doctrine_chunks
from src.agent.environment import EnvironmentClients, synthetic_terrain, synthetic_weather
from src.config import Settings, bool_env
from src.contracts import GeoPoint, Observation, Phase


def test_bool_env_parses_common_truthy_values() -> None:
    assert bool_env("true")
    assert bool_env("1")
    assert not bool_env("false")
    assert bool_env(None, default=True)


def test_synthetic_weather_and_terrain_are_deterministic() -> None:
    geo = GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S")

    weather = synthetic_weather(geo)
    terrain = synthetic_terrain(geo, Phase.mountain)

    assert weather.synthetic
    assert weather.provider == "synthetic"
    assert terrain.synthetic
    assert terrain.terrain_class == "mountain"
    assert "cold_exposure" in terrain.hazards


def test_environment_clients_default_to_synthetic_context() -> None:
    clients = EnvironmentClients(Settings())
    geo = GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S")

    assert clients.weather(geo).synthetic
    assert clients.terrain(geo, Phase.mountain).synthetic


def test_doctrine_lookup_matches_observed_task_code() -> None:
    chunks = lookup_doctrine_chunks(
        [
            Observation(
                soldier_id="Jones",
                task_code="MV-2",
                note="Jones missed Phase Line Bird and gave no SITREP.",
                rating="NOGO",
            )
        ],
        doctrine_refs=[],
    )

    assert [chunk.task_code for chunk in chunks] == ["MV-2"]
    assert chunks[0].source_ref.startswith("asset://doctrine/")

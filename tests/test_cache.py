from src.agent.cache import InMemoryRunLease, NoopRunLease, build_run_lease, redis_health
from src.config import Settings


def test_build_run_lease_defaults_to_noop_without_redis_url() -> None:
    assert isinstance(build_run_lease(Settings()), NoopRunLease)


def test_in_memory_run_lease_blocks_duplicate_run_until_release() -> None:
    leases = InMemoryRunLease()
    first = leases.acquire("run-1")
    second = leases.acquire("run-1")

    assert first.acquired
    assert not second.acquired

    first.release()
    third = leases.acquire("run-1")
    assert third.acquired


def test_redis_health_is_false_without_url() -> None:
    assert not redis_health(None)

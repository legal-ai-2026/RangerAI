from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from src.config import Settings, settings


class LeaseHandle(Protocol):
    acquired: bool

    def release(self) -> None:
        ...


class RunLease(Protocol):
    def acquire(self, run_id: str) -> LeaseHandle:
        ...


@dataclass(frozen=True)
class NoopLeaseHandle:
    acquired: bool = True

    def release(self) -> None:
        return None


@dataclass
class NoopRunLease:
    def acquire(self, _run_id: str) -> LeaseHandle:
        return NoopLeaseHandle()


@dataclass
class InMemoryLeaseHandle:
    run_id: str
    active: set[str]
    acquired: bool

    def release(self) -> None:
        if self.acquired:
            self.active.discard(self.run_id)


@dataclass
class InMemoryRunLease:
    active: set[str] = field(default_factory=set)

    def acquire(self, run_id: str) -> LeaseHandle:
        if run_id in self.active:
            return InMemoryLeaseHandle(run_id=run_id, active=self.active, acquired=False)
        self.active.add(run_id)
        return InMemoryLeaseHandle(run_id=run_id, active=self.active, acquired=True)


@dataclass
class RedisLeaseHandle:
    redis_url: str
    key: str
    token: str
    acquired: bool

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            from redis import Redis

            client = Redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1)
            try:
                client.eval(
                    """
                    if redis.call("get", KEYS[1]) == ARGV[1] then
                        return redis.call("del", KEYS[1])
                    end
                    return 0
                    """,
                    1,
                    self.key,
                    self.token,
                )
            finally:
                client.close()
        except Exception:
            return


@dataclass(frozen=True)
class RedisRunLease:
    redis_url: str
    ttl_seconds: int = 900
    key_prefix: str = "ranger:run-lease"

    def acquire(self, run_id: str) -> LeaseHandle:
        token = str(uuid4())
        key = f"{self.key_prefix}:{run_id}"
        try:
            from redis import Redis

            client = Redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1)
            try:
                acquired = bool(client.set(key, token, nx=True, ex=self.ttl_seconds))
            finally:
                client.close()
            return RedisLeaseHandle(
                redis_url=self.redis_url,
                key=key,
                token=token,
                acquired=acquired,
            )
        except Exception:
            return NoopLeaseHandle()


def build_run_lease(config: Settings = settings) -> RunLease:
    if config.redis_url:
        return RedisRunLease(config.redis_url)
    return NoopRunLease()


def redis_health(redis_url: str | None) -> bool:
    if not redis_url:
        return False
    try:
        from redis import Redis

        client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False

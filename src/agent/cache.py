from __future__ import annotations


def redis_health(redis_url: str) -> bool:
    try:
        from redis import Redis

        client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False

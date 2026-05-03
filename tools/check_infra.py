from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    load_env_file(ROOT / ".env.local")

    from src.agent.cache import redis_health
    from src.agent.store import build_run_store
    from src.agent.vector_store import build_vector_store
    from src.config import settings
    from src.kg.client import KGClient

    vector_store = build_vector_store(settings)
    status = {
        "postgres": build_run_store(settings).health(),
        "pgvector": vector_store.health() if vector_store is not None else False,
        "redis": redis_health(settings.redis_url),
        "falkordb": KGClient().health(),
    }
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if all(status.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

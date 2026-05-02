from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: post_demo_ingest.py API_URL ENVELOPE_PATH", file=sys.stderr)
        return 2

    api_url = sys.argv[1].rstrip("/")
    envelope_path = Path(sys.argv[2])
    payload = envelope_path.read_bytes()
    request = urllib.request.Request(
        f"{api_url}/v1/ingest",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read())
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

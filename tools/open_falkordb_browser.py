from __future__ import annotations

import webbrowser


def main() -> int:
    webbrowser.open("http://localhost:3000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

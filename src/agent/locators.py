from __future__ import annotations


def entity_id_from_locator(locator: str, entity_type: str) -> str | None:
    marker = f"/{entity_type}/"
    if marker not in locator:
        return None
    return locator.split(marker, maxsplit=1)[1].split("#", maxsplit=1)[0] or None

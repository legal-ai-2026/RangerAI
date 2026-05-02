.PHONY: test lint typecheck dev-services

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	uv run mypy src

dev-services:
	docker compose -f docker/compose.dev.yaml up -d

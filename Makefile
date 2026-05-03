.PHONY: test lint typecheck verify dev-services

test:
	env -u MAKEFLAGS -u MFLAGS -u MAKELEVEL uv run python -m pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	uv run --extra dev mypy src

verify: test lint typecheck

dev-services:
	docker compose -f docker/compose.dev.yaml up -d

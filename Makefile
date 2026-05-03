.PHONY: test lint typecheck verify infra-health dev-api smoke dev-services

test:
	env -u MAKEFLAGS -u MFLAGS -u MAKELEVEL uv run python -m pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	uv run --extra dev mypy src

verify: test lint typecheck

infra-health:
	uv run python tools/check_infra.py

dev-api:
	uv run python tools/run_api.py --host 0.0.0.0 --port 8001

smoke:
	uv run python tools/smoke_synthetic.py

dev-services:
	docker compose -f docker/compose.dev.yaml up -d

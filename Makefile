.PHONY: demo test lint typecheck dev-services

API_URL ?= http://localhost:8001
DEMO_ENVELOPE ?= assets/fixtures/envelopes/mountain_phase_amb_01.json

demo:
	python tools/post_demo_ingest.py $(API_URL) $(DEMO_ENVELOPE)

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	uv run mypy src

dev-services:
	docker compose -f docker/compose.dev.yaml up -d

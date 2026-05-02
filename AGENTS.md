# Repository Guidelines

See `AGENT.md` first. It is the operational entry point for AI coding agents and contains the project boundaries, ethics rules, guardrails, and workflow.

## Project Structure & Module Organization

This repository implements the Ranger School adversarial training agent: a LangGraph workflow that turns instructor audio, OR booklet photos, doctrine, and graph context into instructor-approved recommendations. The repo is currently a scaffold; use this target layout:

- `src/api/`: FastAPI routes, WebSocket handlers, and request models.
- `src/agent/`: LangGraph state, nodes, tools, policy gates, and emits.
- `src/kg/`: FalkorDB schema, Cypher writes, seed data, and GraphRAG helpers.
- `src/ingest/`: STT, OCR, entity extraction, and validation adapters.
- `tests/`: unit and integration tests mirroring `src/`.
- `assets/`: doctrine excerpts, terrain fixtures, clips, and OR booklet samples.
- `tools/`: operator helper scripts only; no frontend code.

Keep shared contracts in one module, especially `IngestEnvelope`, `GeoPoint`, and `ScenarioRecommendation`.

## Build, Test, and Development Commands

Use `uv` once `pyproject.toml` is added:

- `uv sync`: install pinned dependencies.
- `docker compose -f docker/compose.dev.yaml up -d`: start graph and cache services.
- `uv run uvicorn src.api.main:app --reload --port 8001`: run the local API.
- `uv run pytest`: run tests.
- `uv run ruff check .` and `uv run ruff format .`: lint and format Python.

Document replacements in `README.md` and keep this file in sync.

## Coding Style & Naming Conventions

Use Python 3.11+, Pydantic v2, and typed interfaces at every boundary. Set `extra="forbid"` on inbound contracts. Use 4-space indentation for Python and 2-space indentation for JSON, YAML, and Markdown files.

Prefer explicit names: `extract_observations`, `write_recommendation_edges`, `fairness_score`. Use snake_case for modules/functions, PascalCase for Pydantic models, and SCREAMING_SNAKE_CASE for constants. Keep LangGraph nodes small.

## Testing Guidelines

Use `pytest`. Name tests by behavior, for example `test_rejects_hallucinated_soldier` or `test_blocks_unsafe_cold_water_recommendation`. Cover schema validation, idempotent FalkorDB `MERGE` writes, policy filters, fairness counters, and interrupt-gated emits. Mock external APIs in unit tests.

## Commit & Pull Request Guidelines

This repository has no commit history yet. Use concise, imperative subjects such as `Add LangGraph ingest chain` or `Seed TC task schema`. Keep unrelated changes separate.

Pull requests must include a summary, tests run, linked issue or operational milestone, and screenshots or clips for dashboard changes. Call out schema migrations, environment variables, policy changes, and new runtime APIs.

## Security & Configuration Tips

Do not commit secrets, tokens, roster exports, real instructor audio, or unredacted student data. Store local settings in ignored `.env.local` files and document keys in `.env.example`. Scrub PII and OPSEC-sensitive text before LLM calls, validate soldiers against the roster, and keep instructor approval mandatory before emits.

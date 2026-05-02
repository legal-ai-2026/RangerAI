# AGENT.md — Adversarial Training Agent

This file is the entry point for AI coding agents working in this repository. Read it before making changes and keep it current.

## What This Project Is

**Spire Adversarial Training Agent** is the System 1 backend microservice for the xTech National Security Hackathon Track 3 mission-command demo. It ingests a Ranger Instructor's voice notes, photographed Operational Readiness booklet pages, and optional typed notes, then emits frontend-ready performance summaries and typed, doctrinally cited, fairness-checked `ScenarioRecommendation` objects for instructor approval.

Systems 2 and 3 are separate microservices. This service should communicate with them only through documented REST contracts in `docs/architecture.md`.

## What This Project Is Not

- Not a frontend. Do not add React, Tailwind, shadcn, or browser app code here. Provide dashboard data through typed API responses.
- Not a chatbot. The service runs once per `IngestEnvelope`, produces typed outputs, and stops.
- Not a weapon system. It supports training decisions only. Preserve the DoD AI principles: Responsible, Equitable, Traceable, Reliable, and Governable.

## Read First

1. `README.md` for quickstart and API flow.
2. `docs/architecture.md` for agent loop, contracts, and guardrails.
3. `docs/implementation.md` for build priorities, cut list, and demo path.
4. `assets/ground-truth/` if present. Advisor notes override docs on Ranger workflow.

## Local Run

```bash
uv venv && source .venv/bin/activate
uv sync --extra dev
docker compose -f docker/compose.dev.yaml up -d
cp .env.example .env
uv run uvicorn src.api.main:app --reload --port 8001
```

`make demo` posts `assets/fixtures/envelopes/mountain_phase_amb_01.json` to the local API.

## Tests

```bash
uv run pytest
uv run pytest tests/test_policy.py
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

Add prompt regression tests under `tests/prompts/` before changing guardrails or model prompts.

## Current Layout

- `src/api/`: FastAPI routes.
- `src/agent/`: workflow, policy, run store.
- `src/contracts.py`: Pydantic contracts.
- `src/ingest/`: STT, OCR, LLM, and scrubber adapters.
- `src/kg/`: FalkorDB client and Cypher writes.
- `docs/`: architecture and implementation notes.
- `assets/`: fixtures, doctrine, and ground-truth examples.
- `tools/`: operator/demo helper scripts only.

## Coding Rules

- Python 3.11+ with type hints; Pydantic v2 for all contracts.
- Use `extra="forbid"` on inbound models.
- Use `async def` for network, model API, or database paths.
- Use snake_case for modules/functions, PascalCase for classes/models, and SCREAMING_SNAKE_CASE for constants.
- Pin every dependency in `pyproject.toml`.
- Use OpenAI `whisper-1` for primary STT and OpenAI `gpt-4o` for primary picture/OCR interpretation unless a documented change is approved.
- Do not use emojis in code, logs, prompts, or doctrine output.

## PR Rejection Triggers

1. A model API call with an unpinned model string.
2. Any recommendation path that bypasses policy filtering or instructor approval.
3. Logging secrets, real student data, precise MGRS coordinates, real OR scans, or real audio clips.
4. Adding dependencies without pinning and explaining them.
5. Removing tests without a `test:` commit subject and a reason.
6. Guardrail changes without tests or prompt-regression coverage.

## DoD AI Ethics Mapping

| Principle | Repository Rule |
|---|---|
| Responsible | The agent recommends; the instructor decides. |
| Equitable | Fairness scoring is mandatory. |
| Traceable | Recommendations carry `doctrine_refs`. |
| Reliable | Uncertain OCR stays reviewable and should not silently enter the KG. |
| Governable | Approval and rejection are explicit API actions. |

## Cross-System Reminders

- Inbound from System 3: `POST /v1/lessons-learned` should be idempotent on `lesson_id` when implemented.
- Outbound to System 2: `GET /v1/soldier/{id}/training-trajectory` is read-only when implemented.
- Canonical IDs are `soldier_id`, `patrol_id`, `mission_id`, and `platoon_id`. Do not mint local replacements.

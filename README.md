# C2D2 AVAI Ranger Agent

API-only deployable implementation of System 1: a Ranger School adversarial training agent that ingests instructor notes, audio, and OR booklet imagery, writes observations to FalkorDB, drafts doctrinally cited recommendations, and requires instructor approval before emit.

## Required Infrastructure Before Running

The main FastAPI app can run locally or in Kubernetes. This repository includes
a staging app manifest at `k8s/staging/deployment.yaml`; supporting
infrastructure is still expected to be provisioned separately and reached
through configured service names or connection strings.

Before running the app, provision these services and make them reachable from
the machine or runtime where the API process runs:

- Postgres with the `pgvector` extension enabled for durable run state, approval
  records, audit data, and vector retrieval.
- Redis for workflow checkpointing, locks, cache, and short-lived coordination.
- FalkorDB for the mission knowledge graph.

Recommended Kubernetes resources for the supporting infrastructure:

- `Namespace` for the infra stack, for example `c2d2-infra`.
- Postgres/pgvector: `StatefulSet`, `Service`, `PersistentVolumeClaim`,
  `Secret`, `ConfigMap`, and an init or migration `Job` that runs
  `CREATE EXTENSION IF NOT EXISTS vector;`.
- Redis: `StatefulSet` or `Deployment`, `Service`, `Secret`, `ConfigMap`, and an
  optional `PersistentVolumeClaim` if checkpoint durability matters.
- FalkorDB: `StatefulSet`, `Service`, `PersistentVolumeClaim`, and any supported
  auth or ACL configuration.
- Operational controls: `NetworkPolicy`, `PodDisruptionBudget`, `ResourceQuota`,
  and `LimitRange`.

For operational use, expose these services through private networking such as a
VPN, private load balancers, or another restricted network path. Do not expose
Postgres, Redis, or FalkorDB publicly without authentication, TLS where
supported, and network allowlists.

Add the final connection values to your local environment before running the
app. The exact values depend on your cluster and network setup:

```env
SYSTEM1_API_KEY=
CORS_ALLOW_ORIGINS=

DATABASE_URL=
PGVECTOR_CONNECTION_STRING=
POSTGRES_HOST=
POSTGRES_PORT=5432
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_SSLMODE=require
EMBEDDING_DIMENSIONS=1536

REDIS_URL=redis://:password@host:6379/0

FALKORDB_HOST=
FALKORDB_PORT=6379
FALKORDB_GRAPH=ranger
FALKORDB_URL=
FALKORDB_USERNAME=
FALKORDB_PASSWORD=

WEATHER_PROVIDER=synthetic # synthetic, nws, or open_meteo
TERRAIN_PROVIDER=synthetic # synthetic or usgs_epqs
NWS_USER_AGENT=
ENVIRONMENT_TIMEOUT_SECONDS=3
ALLOW_SYNTHETIC_ENVIRONMENT_FALLBACK=true
```

Current code uses Postgres for run storage when the Postgres environment values
are configured; otherwise it falls back to an in-memory store for local
development. Redis is used for run-level workflow leases when `REDIS_URL` is
configured. FalkorDB stores mission graph observations and approved
recommendation provenance. A pgvector document store adapter exists for future
doctrine and observation retrieval. Weather and terrain enrichment default to
synthetic deterministic fixtures; live NWS/Open-Meteo weather and USGS EPQS
terrain hooks can be enabled by environment. Redis-backed LangGraph
checkpointing and embedding ingestion are still pending.

For local infrastructure only, use `docker/compose.dev.yaml`. The main app is
started separately with `uvicorn`.

## Kubernetes Staging Deployment

Woodpecker builds and deploys the staging app on `main` branch pushes using
`.woodpecker.yaml`. The pipeline runs lint and tests, builds the image with
Kaniko, pushes `latest` and the short SHA tag to the cluster registry, replaces
`IMAGE_TAG` in `k8s/staging/deployment.yaml`, applies the manifest, and waits
for the rollout.

Create the staging API secret before the first deploy:

```bash
kubectl -n staging create secret generic c2d2-avai-secrets \
  --from-literal=SYSTEM1_API_KEY='replace-me'
```

Add provider and infrastructure keys to the same secret as needed, for example
`OPENAI_API_KEY`, `DATABASE_URL`, `PGVECTOR_CONNECTION_STRING`,
`POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`FALKORDB_URL`, `FALKORDB_USERNAME`, `FALKORDB_PASSWORD`, and Langfuse keys.
The staging ConfigMap defaults to synthetic weather/terrain,
`FALKORDB_HOST=falkordb`, and `REDIS_URL=redis://redis:6379/0`.

## Local Run

1. Copy environment settings:

   ```bash
   cp .env.example .env
   ```

2. Fill required provider keys and infrastructure connection values in `.env`.
   `OPENAI_API_KEY` is the primary key for Whisper STT and GPT-4o image/OCR
   interpretation. Anthropic, Deepgram, Mistral, and OpenWeather remain optional
   integrations/fallbacks.

3. Install dependencies:

   ```bash
   uv sync --extra dev
   ```

4. Run the API process:

   ```bash
   uv run python tools/run_api.py --host 0.0.0.0 --port 8001 --reload
   ```

   The helper loads `.env` and then `.env.local` before importing the app, which
   avoids shell-specific environment sourcing issues.

5. Open API docs at `http://localhost:8001/docs`.

## Local Development

```bash
uv sync --extra dev
docker compose -f docker/compose.dev.yaml up -d
make dev-api
uv run pytest
uv run ruff check .
uv run --extra dev mypy src
```

Run the full local gate:

```bash
make verify
```

Check configured infrastructure connectivity without printing credentials:

```bash
make infra-health
```

Run the synthetic API smoke loop against a running local API without provider
or OpenAI smoke calls:

```bash
make smoke
```

Run an in-memory longitudinal synthetic smoke that does not need a running API
or provider keys:

```bash
uv run python tools/smoke_longitudinal_synthetic.py
```

It loads `assets/fixtures/envelopes/longitudinal_company_01.json`, processes
two platoons across eight missions, approves pending recommendations with
synthetic rationale, and prints compact mission-state and soldier-trajectory
summaries.

Run the same fixture as an evaluation gate against expected observations,
recommendation opportunities, policy invariants, and decision-support metadata:

```bash
uv run python tools/smoke_longitudinal_synthetic.py --evaluate
```

Use `--min-score`, `--require-llm`, and `--fail-on-fallback` to tighten the gate
for provider-backed validation runs.

To exercise live OpenAI-backed extraction and recommendation ranking on the
same fixture, load `OPENAI_API_KEY` and run:

```bash
OPENAI_REASONING_MODEL=gpt-5.5 uv run python tools/smoke_longitudinal_synthetic.py --provider openai --openai-model gpt-5.5 --require-llm
```

Use `--openai-model gpt-5.5-pro` for pro-model checks; reasoning calls retry
through the Responses API when a model is not accepted by Chat Completions. For
practical pro runs, keep `OPENAI_EXTRACTION_MODEL` on a faster model and reserve
`OPENAI_REASONING_MODEL=gpt-5.5-pro` for recommendation ranking.

```bash
OPENAI_EXTRACTION_MODEL=gpt-5.5 OPENAI_REASONING_MODEL=gpt-5.5-pro uv run python tools/smoke_longitudinal_synthetic.py --provider openai --openai-extraction-model gpt-5.5 --openai-model gpt-5.5-pro --limit 1 --require-llm
```

## API Flow

- `POST /v1/ingest` returns a run id and processes STT/OCR/extraction/reasoning in a background task.
- `GET /v1/runs/{run_id}` returns transcript, OCR pages, observations, KG write summary, recommendation records, and errors.
- `GET /v1/runs/{run_id}/audit` returns durable run lifecycle and recommendation decision events.
- `GET /v1/dashboard/runs/{run_id}` returns frontend-ready platoon and soldier performance metrics plus active recommendations.
- `GET /v1/missions/{mission_id}/state` returns a compact mission-command state projection.
- `GET /v1/missions/{mission_id}/team-calibration-profile` returns a derived mission/platoon cue-outcome calibration summary.
- `GET /v1/entities/soldiers/{soldier_id}` returns System 1's read-only projection for a soldier ID.
- `GET /v1/entities/missions/{mission_id}` returns System 1's read-only projection for a mission ID.
- `GET /v1/soldiers/{soldier_id}/performance` returns soldier-facing performance metrics and instructor-approved recommendations.
- `GET /v1/soldier/{soldier_id}/training-trajectory` returns a System 2-facing read-only training trajectory projection.
- `POST /v1/recommendations/{recommendation_id}/feedback` records post-inject calibration feedback for approved recommendations.
- `GET /v1/soldiers/{soldier_id}/calibration-profile` returns deterministic cue/outcome calibration summaries.
- `GET /v1/recommendations/recent` returns recent recommendation records, optionally filtered by mission or status.
- `GET /v1/recommendations/{recommendation_id}` returns one recommendation with run, mission, policy, and status context.
- `POST /v1/recommendations/{recommendation_id}/decision` records instructor approval, edited approval, or rejection.
- `GET /v1/graph/subgraph` returns a frontend-ready graph projection around a run, mission, soldier, or recent state.
- `GET /v1/outbox` returns pending integration events for external workers.
- `GET /v1/update-ledger` returns append-only observation, recommendation, and lesson-signal update records.
- `POST /v1/outbox/{event_id}/published` marks an outbox event as published.
- `POST /v1/lessons-learned` records an idempotent System 3 lesson signal receipt keyed by `lesson_id`.
- `GET /v1/healthz` reports configured providers, FalkorDB health, and LangGraph importability.
- `GET /v1/readyz` reports critical dependency readiness for the running API.

Operational routes accept an optional `X-Trace-Id` request header. When omitted,
the API generates one and echoes it back as `X-Trace-Id`; run records, audit
events, outbox events, and update-ledger entries carry the trace id.

Recommendations are generated from a curated, retrieval-first scenario
intervention library before model-assisted ranking. When `OPENAI_API_KEY` is
configured, the reasoning layer uses the enriched run context to rank and
explain library candidates; invalid or unavailable model output falls back to
the deterministic library draft. Each recommendation can carry an
`intervention_id`, `learning_objective`, rationale subfields, evidence and
model context refs, uncertainty refs, and `score_breakdown` so instructors can
inspect learning value, doctrinal fit, utility, observability, safety, fatigue,
uncertainty, fairness, and repetition tradeoffs before approval. The
decision-science layer also adds `decision_frame`, `decision_quality`,
`value_of_information`, and `review_requirements`; edited, uncertain, or
medium-risk approvals require a recorded `decision_rationale`. Calibration
feedback can focus future cue prompts and add review friction, but it never
bypasses policy filtering or instructor approval.

## Safety Defaults

The API validates payloads with Pydantic v2, scrubs common PII patterns before LLM calls, validates targets against the observed roster, blocks high-risk recommendations, scores fairness spread, requires review acknowledgements where needed, and never emits without instructor approval.

## OpenAI Provider Defaults

- STT: `whisper-1`
- OR booklet photo/OCR interpretation: `gpt-4o`
- Observation extraction: `gpt-5.4-mini`
- Recommendation ranking/rationale: `gpt-5.4-mini`
- Recommendation extraction can still fall back to deterministic local heuristics for tests and local smoke runs without provider keys.

## Agent Guidance

Read `AGENT.md` before substantial changes. It records project boundaries, PR rejection triggers, DoD AI ethics mapping, and cross-system contract reminders.

For cross-app shared data rules, canonical IDs, provenance requirements, and
drift tracking, use `docs/shared-data-contract.md`.

For doctrine RAG, free weather and terrain providers, synthetic fallback policy,
and planner boundaries, use `docs/doctrine-rag-weather-terrain.md`.

For frontend integration flows, endpoint usage, visibility boundaries, and UI
state guidance, use `docs/frontend-integration.md`.

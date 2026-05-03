# C2D2 AVAI Ranger Agent

API-only deployable implementation of System 1: a Ranger School adversarial training agent that ingests instructor notes, audio, and OR booklet imagery, writes observations to FalkorDB, drafts doctrinally cited recommendations, and requires instructor approval before emit.

## Required Infrastructure Before Running

The main FastAPI app is not deployed to Kubernetes. Kubernetes is expected to
host only the supporting infrastructure that the app connects to from outside
the cluster.

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
```

Current code uses Postgres for run storage when the Postgres environment values
are configured; otherwise it falls back to an in-memory store for local
development. Redis is used for run-level workflow leases when `REDIS_URL` is
configured. A pgvector document store adapter exists for future doctrine and
observation retrieval. Redis-backed LangGraph checkpointing and embedding
ingestion are still pending.

For local infrastructure only, use `docker/compose.dev.yaml`. The main app is
started separately with `uvicorn`.

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
   uv run uvicorn src.api.main:app --reload --port 8001
   ```

5. Open API docs at `http://localhost:8001/docs`.

## Local Development

```bash
uv sync --extra dev
docker compose -f docker/compose.dev.yaml up -d
uv run uvicorn src.api.main:app --reload --port 8001
uv run pytest
uv run ruff check .
uv run --extra dev mypy src
```

Run the full local gate:

```bash
make verify
```

## API Flow

- `POST /v1/ingest` returns a run id and processes STT/OCR/extraction/reasoning in a background task.
- `GET /v1/runs/{run_id}` returns transcript, OCR pages, observations, KG write summary, recommendation records, and errors.
- `GET /v1/runs/{run_id}/audit` returns durable run lifecycle and recommendation decision events.
- `GET /v1/dashboard/runs/{run_id}` returns frontend-ready platoon and soldier performance metrics plus active recommendations.
- `POST /v1/recommendations/{recommendation_id}/decision` records instructor approval or rejection.
- `GET /v1/outbox` returns pending integration events for external workers.
- `POST /v1/outbox/{event_id}/published` marks an outbox event as published.
- `GET /v1/healthz` reports configured providers, FalkorDB health, and LangGraph importability.

## Safety Defaults

The API validates payloads with Pydantic v2, scrubs common PII patterns before LLM calls, validates targets against the observed roster, blocks high-risk recommendations, scores fairness spread, and never emits without instructor approval.

## OpenAI Provider Defaults

- STT: `whisper-1`
- OR booklet photo/OCR interpretation: `gpt-4o`
- Recommendation extraction can still fall back to deterministic local heuristics for tests and local smoke runs without provider keys.

## Agent Guidance

Read `AGENT.md` before substantial changes. It records project boundaries, PR rejection triggers, DoD AI ethics mapping, and cross-system contract reminders.

# Architecture — Adversarial Training Agent (System 1)

## Purpose

This service ingests a Ranger Instructor's voice notes, photographed Operational Readiness (OR) booklet pages, and optional typed notes. It fuses those inputs with a live FalkorDB knowledge graph and emits doctrinally cited, fairness-checked `ScenarioRecommendation` objects for adjusting an in-progress Ranger School scenario.

The instructor remains the decision maker. The service gives faster, evidence-cited situational awareness across the platoon; it does not replace Ranger Instructor judgment.

## Operating Context

Ranger School runs three phases: Benning, Mountain, and Florida. Students are evaluated against TC 3-21.76 patrolling tasks and OR booklet GO/NOGO checklists. The current paper-heavy workflow is qualitative and lossy between phases. System 1 preserves instructor control while making observations, doctrine grounding, and fairness checks machine-readable.

## High-Level Flow

```text
Frontend or caller
  POST /v1/ingest
    -> FastAPI ingress
    -> input validation and PII scrub
    -> STT branch + OCR/photo branch
    -> extract typed observations
    -> idempotent FalkorDB MERGE writes
    -> enrich with weather, terrain, doctrine, and history
    -> reason with Claude Sonnet 4.5 and tools
    -> deterministic policy filter
    -> instructor approval gate
    -> emit final recommendation and audit event
```

The current implementation exposes the API-only backend and keeps the workflow in `src.agent.workflow.RangerWorkflow`.

## Agent Loop

| Node | Responsibility | Why it matters |
|---|---|---|
| `validate` | Pydantic-parse `IngestEnvelope`, reject malformed input, scrub PII | First defense layer |
| `stt` | OpenAI Whisper (`whisper-1`) primary; Deepgram remains an optional fallback | Transcription quality drives downstream quality |
| `ocr` | OpenAI multimodal vision (`gpt-4o`) primary; Mistral/Claude remain optional fallbacks | Preserves handwritten/table structure |
| `extract` | Convert transcript/OCR/free text into `Observation[]` and task evaluations | Single source of truth before KG write |
| `kg_write` | Idempotent `MERGE` into FalkorDB with vector-ready observations | Retries must not duplicate facts |
| `enrich` | Retrieve weather, terrain, doctrine, student history, fairness counts | Gives the reasoner facts, not guesses |
| `reason` | Claude Sonnet 4.5 with tool use; emits draft recommendations | The game-master step |
| `policy_filter` | Safety, fairness, doctrine grounding, OPSEC checks | Blocks obvious failures before review |
| `human_gate` | Instructor approve/edit/reject, target LangGraph `interrupt()` | Human judgment anchor |
| `emit` | Persist final recommendation, audit, broadcast | Only publish path |

LangGraph is the target orchestrator because `interrupt()` maps cleanly to instructor approval, checkpointers support deterministic replay, and this is one agent with tools rather than a multi-agent crew.

## Tools

The reason node may call:

- `kg.query_cypher(query, params)` for graph reads.
- `kg.vector_search(text, k, label)` for semantic search over observations or doctrine.
- `weather.current(lat, lon)` and `weather.forecast_24h(lat, lon)` for safety checks.
- `terrain.lookup(mgrs)` for terrain class, slope, and water features.
- `doctrine.lookup(query)` for TC 3-21.76 RAG.
- `student.history(soldier_id, window_days)` for recent GO/NOGO trend and role rotation.
- `platoon.curveball_count(platoon_id, window_h)` for fairness.

`recommend.draft` is not a free-form tool; it is the typed final output schema.

## Knowledge Graph

FalkorDB stores canonical mission facts.

Core labels: `Soldier`, `Platoon`, `Patrol`, `Mission`, `Phase`, `Task`, `Observation`, `TaskEval`, `Weather`, `TerrainSegment`, `Recommendation`, and future `DoctrineChunk`.

Core relationships:

```cypher
(Soldier)-[:MEMBER_OF]->(Patrol)-[:PART_OF]->(Mission)-[:IN_PHASE]->(Phase)
(Soldier)-[:HAS_OBSERVATION {timestamp}]->(Observation)-[:ON_TASK]->(Task)
(Patrol)-[:EVALUATED_AS {rating}]->(TaskEval)-[:OF_TASK]->(Task)
(Mission)-[:OBSERVED_WX]->(Weather)
(Mission)-[:IN_TERRAIN]->(TerrainSegment)
(Recommendation)-[:TARGETS]->(Soldier)
(Recommendation)-[:DERIVED_FROM]->(Observation)
(Recommendation)-[:CITES]->(Task)
```

Indexes:

```cypher
CREATE INDEX FOR (s:Soldier) ON (s.soldier_id);
CREATE INDEX FOR (p:Platoon) ON (p.platoon_id);
CREATE INDEX FOR (o:Observation) ON (o.timestamp);
CALL db.idx.fulltext.createNodeIndex('Observation', 'note');
CALL db.idx.vector.createNodeIndex('Observation', 'embedding', 1536, 'COSINE');
CALL db.idx.vector.createNodeIndex('Task', 'embedding', 1536, 'COSINE');
```

Seed `Task` data from TC 3-21.76 tasks such as PB-1 perimeter, PB-3 OPORD, PB-7 50-percent security, MV-2 phase-line/SITREPs, and AM-4 ambush initiation.

## Data Contracts

Shared IDs, provenance rules, store ownership, and drift tracking are defined in
`docs/shared-data-contract.md`. All Systems 1, 2, and 3 integrations should use
that document as the cross-app contract.

Inbound `IngestEnvelope` includes `envelope_id`, `instructor_id`, `platoon_id`, `mission_id`, `phase`, `timestamp_utc`, `geo`, optional `audio_b64`, `image_b64[]`, and optional `free_text`. Inbound models use Pydantic v2 with `extra="forbid"`.

Outbound `ScenarioRecommendation` includes `target_soldier_id`, `rationale`, `development_edge`, `proposed_modification`, non-empty `doctrine_refs`, `safety_checks`, `estimated_duration_min`, `requires_resources`, `risk_level`, and `fairness_score`.

Canonical IDs are `soldier_id`, `patrol_id`, `mission_id`, and `platoon_id`. Do not mint local substitutes for cross-system entities.

## Guardrails

Every recommendation passes through:

- Input filter: PII/OPSEC scrub before model calls; target additions include GLiNER, Llama Guard 4 multimodal, and NeMo jailbreak rails.
- Policy filter: weather safety, fairness counter, doctrine grounding, and roster validation.
- Output filter: target additions include Llama Guard 4 and domain safety classifiers.
- Human gate: instructor approve/edit/reject before emit.
- Audit log: target hash-chain entry with trace id, policy outcome, model/tool calls, and decision.

Failure modes to catch include cold-water immersion risk, doctrine contradiction, repeated targeting of the same soldier, hallucinated soldier names, smudged-page GO/NOGO hallucinations, precise MGRS leakage, and prompt injection in instructor input.

## API Contracts

Versioned paths are canonical:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/ingest` | Submit an envelope and start processing |
| `GET` | `/v1/runs/{run_id}` | Inspect run state and pending recommendations |
| `GET` | `/v1/runs/{run_id}/audit` | Inspect run lifecycle and instructor decision audit events |
| `GET` | `/v1/dashboard/runs/{run_id}` | Frontend-ready platoon and soldier performance summary |
| `POST` | `/v1/recommendations/{id}/decision` | Instructor approve/reject/edit decision |
| `GET` | `/v1/outbox` | Poll pending integration events |
| `POST` | `/v1/outbox/{event_id}/published` | Mark an integration event as published |
| `GET` | `/v1/soldier/{id}/training-trajectory` | Future read-only endpoint for System 2 |
| `POST` | `/v1/lessons-learned` | Future idempotent webhook from System 3 |
| `GET` | `/v1/healthz` | Dependency and configuration health |

## Tech Stack

Pinned core stack: LangGraph 1.1.0, OpenAI 1.54.0, Anthropic SDK 0.45.0, Instructor 1.7.0, Deepgram SDK 4.1.0, Mistral AI 1.5.0, FalkorDB 1.2.0, Redis 5.2.0, FastAPI 0.115.6, Pydantic 2.10.4, Uvicorn 0.34.0, Langfuse 2.60.0, Tenacity 9.0.0.

Pinned model constants live in `src.agent.models`.

OpenAI defaults are `whisper-1` for STT and `gpt-4o` for multimodal picture interpretation.

## Observability and Audit

Every request should receive a `trace_id` that propagates to Langfuse and to a tamper-evident audit log. Demo audit entries can live locally; production should use object-lock storage. Logs must redact secrets, full names where prohibited, precise MGRS, real audio/image data, and anything marked `PERSREL` or `PRVCY`.

## Deployment Posture

The app process runs outside the Kubernetes infrastructure stack and connects to managed or cluster-hosted Postgres/pgvector, Redis, FalkorDB, Langfuse, and audit storage. Later stages target GovCloud/IL5-compatible deployments.

## Deliberate Non-Goals

This service does not autonomously change schedules, retain raw audio after STT, retain images after OCR, call Systems 2 or 3 outbound during the agent loop, or produce free-form chat.

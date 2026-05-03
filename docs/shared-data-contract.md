# Shared Data Contract

This document is for all services that share the graph-stack infrastructure:
System 1 Ranger adversarial training agent, System 2 training trajectory service,
System 3 lessons-learned service, and the frontend.

The rule is simple: the frontend sends canonical IDs, services resolve details
from shared stores, and every agentic output must cite the exact data records
that caused it.

## System 1 Project Profile

This repository is the System 1 Ranger adversarial training agent. It is an
API-only backend, not a frontend and not a long-running chatbot. It receives an
instructor ingest envelope, extracts observations, writes graph facts, drafts
recommendations, applies policy checks, waits for instructor approval, and emits
decision events for the other systems.

| Area | Detail |
|---|---|
| Service name | System 1 Ranger adversarial training agent |
| Runtime posture | Main app runs outside Kubernetes; shared Postgres, Redis, pgvector, and FalkorDB may run inside Kubernetes |
| Public API root | `/v1` |
| Primary input | `IngestEnvelope` submitted to `POST /v1/ingest` |
| Primary output | `RunRecord` with observations, recommendation records, audit events, dashboard summary, and outbox events |
| Human gate | Recommendations must be approved or rejected through `/v1/recommendations/{recommendation_id}/decision` |
| System 1-owned IDs | `run_id`, `observation_id`, `recommendation_id`, `event_id` for its own runs/events |
| Externally owned IDs | `soldier_id`, `instructor_id`, `platoon_id`, `patrol_id`, `mission_id`, `task_code` |
| Source of truth this service writes | Run state, audit events, outbox events, observations, and approved recommendation graph nodes |
| Source of truth this service does not own | Roster/person profiles, mission plans, training trajectory profiles, lessons-learned records |

Other projects should treat this service as the owner of short-horizon
scenario observations and instructor-approved scenario recommendations. They
should not write into `ranger_runs`, `ranger_audit_events`, or
`ranger_outbox_events` directly.

## System 1 API For Other Projects

| Method | Path | Caller | Purpose | Important response data |
|---|---|---|---|---|
| `GET` | `/v1/healthz` | Frontend, operators, other services | Check app, provider, and infra availability | `dependencies_available`, `providers_configured`, configured OpenAI model names |
| `POST` | `/v1/ingest` | Frontend or instructor workflow | Start a new processing run | `run_id`, initial `status=accepted`, original `ingest` |
| `GET` | `/v1/runs/{run_id}` | Frontend, System 2, System 3 | Fetch canonical System 1 run state | `observations`, `recommendations`, `kg_write_summary`, `errors` |
| `GET` | `/v1/dashboard/runs/{run_id}` | Frontend | Fetch presentation-neutral summary | per-soldier GO/NOGO counts, readiness score, active recommendations |
| `GET` | `/v1/runs/{run_id}/audit` | Frontend, System 2, System 3 | Inspect lifecycle and decision events | immutable `AuditEvent[]` ordered by timestamp |
| `POST` | `/v1/recommendations/{recommendation_id}/decision` | Frontend/instructor workflow | Approve or reject a recommendation | `ApprovalResponse` with final status |
| `GET` | `/v1/outbox` | System 2, System 3, integration workers | Poll pending System 1 decision events | `OutboxEvent[]` |
| `POST` | `/v1/outbox/{event_id}/published` | System 2, System 3, integration workers | Mark a consumed event as published | `event_id`, `status=published` |

The frontend may call the decision endpoint by `recommendation_id` only. This
service resolves the owning `run_id` from its run store.

Future endpoints named in architecture but not implemented yet:

- `GET /v1/soldier/{id}/training-trajectory`
- `POST /v1/lessons-learned`

Until those endpoints exist, System 2 and System 3 should exchange trajectory
and lesson data through the shared stores and outbox/update-ledger pattern in
this document.

## System 1 State Machine

Run statuses:

| Status | Meaning | Who should act |
|---|---|---|
| `accepted` | Ingest was validated and persisted | System 1 background processor |
| `processing` | STT/OCR/extraction/KG/reasoning/policy are running | System 1 |
| `pending_approval` | Recommendations are ready for instructor decision | Frontend/instructor |
| `completed` | All recommendations are approved, rejected, or blocked | System 2/System 3 may consume outbox events |
| `failed` | Processing failed; inspect `errors` and audit events | Operator or caller |

Recommendation statuses:

| Status | Meaning | Downstream rule |
|---|---|---|
| `pending` | Policy allowed the recommendation; instructor has not decided | Do not treat as approved training intent |
| `approved` | Instructor approved it | Systems 2 and 3 may use it as a decision signal |
| `rejected` | Instructor rejected it | Systems 2 and 3 may use it as negative feedback |
| `blocked` | Policy rejected it before instructor approval | Do not approve or execute; use reasons for safety/fairness analysis |

## System 1 Contract Shapes

Inbound `IngestEnvelope`:

| Field | Type | Meaning |
|---|---|---|
| `envelope_id` | string | Caller-supplied or generated envelope identifier |
| `instructor_id` | string | Canonical instructor/operator ID |
| `platoon_id` | string | Canonical platoon ID |
| `mission_id` | string | Canonical mission/scenario ID |
| `phase` | `Benning`, `Mountain`, or `Florida` | Ranger School phase |
| `timestamp_utc` | timezone-aware datetime | Observation time |
| `geo` | object | `lat`, `lon`, and `grid_mgrs` |
| `audio_b64` | string or null | Optional instructor audio |
| `image_b64` | string array | Optional OR booklet/page photos |
| `free_text` | string or null | Optional typed instructor note, max 20,000 chars |

At least one of `audio_b64`, `image_b64`, or `free_text` is required.

Derived `Observation`:

| Field | Type | Meaning |
|---|---|---|
| `observation_id` | string | System 1 atomic fact ID |
| `soldier_id` | string | Canonical target soldier ID |
| `task_code` | string | Doctrine/OR task code; `UNMAPPED` when unclear |
| `note` | string | Redacted observation text |
| `rating` | `GO`, `NOGO`, or `UNCERTAIN` | Instructor/model-derived task assessment |
| `timestamp_utc` | datetime | Observation timestamp |
| `source` | `audio`, `image`, `free_text`, or `synthetic` | Source branch |

`ScenarioRecommendation`:

| Field | Type | Meaning |
|---|---|---|
| `recommendation_id` | string | System 1 recommendation ID |
| `target_soldier_id` | string | Canonical soldier ID |
| `rationale` | string | Why the scenario modification is proposed |
| `development_edge` | enum | Development area such as `communications` or `fire_control` |
| `proposed_modification` | string | Instructor-approved training inject proposal |
| `doctrine_refs` | string array | Human-readable doctrine references |
| `safety_checks` | string array | Safety constraints and checks |
| `estimated_duration_min` | integer | Estimated duration, 5 to 240 minutes |
| `requires_resources` | string array | Extra resources needed |
| `risk_level` | `low`, `medium`, or `high` | Risk classification |
| `fairness_score` | number | Policy score from 0 to 1 |

`RecommendationRecord` wraps a recommendation with:

- `policy.allowed`
- `policy.reasons`
- `policy.fairness_score`
- `status`

`OutboxEvent.payload` currently contains:

```json
{
  "recommendation_id": "rec-123",
  "status": "approved",
  "target_soldier_id": "Jones"
}
```

Systems 2 and 3 should resolve the full run, observation, recommendation, and
audit context by reading `GET /v1/runs/{run_id}` and
`GET /v1/runs/{run_id}/audit` after receiving an outbox event.

## Shared Infrastructure

| Store | Shared role | Write rule |
|---|---|---|
| Postgres | Durable run state, audit events, outbox events, snapshots, update ledgers | Append-only for audits/updates; mutable only for current materialized state |
| pgvector | Semantic retrieval over doctrine, observations, lessons, and summaries | Documents are upserted by namespace and `document_id`; source metadata is mandatory |
| FalkorDB | Canonical relationship graph for people, units, missions, tasks, observations, recommendations | Use idempotent `MERGE`; never mint substitute person or mission IDs |
| Redis | Leases, locks, checkpoints, rate counters, short-lived cache | TTL required for coordination keys |

## System 1 Data Mutations Implemented Here

This repository is System 1. It changes shared data only through the FastAPI
workflow and the store adapters in `src/agent/`, `src/kg/`, and `src/api/`.
The main app runs outside Kubernetes; the shared infrastructure stores may run
inside the cluster.

| Trigger | Store | Record changed | How it changes data |
|---|---|---|---|
| `POST /v1/ingest` | Postgres `ranger_runs` | `RunRecord` keyed by `run_id` | Inserts an accepted run with the inbound `IngestEnvelope`; later processing updates the same row's `status` and JSON `record` |
| `POST /v1/ingest` | Postgres `ranger_audit_events` | `run_accepted` | Appends an immutable event with `mission_id`, `platoon_id`, `phase`, and `instructor_id` as actor |
| Background processing starts | Redis | `ranger:run-lease:{run_id}` | Creates a 900-second lease with `SET NX EX`; deletes it on release if the token still matches |
| Background processing starts | Postgres `ranger_audit_events` | `run_processing_started` | Appends an immutable event before STT/OCR/extraction work begins |
| STT/OCR/extraction completes | Postgres `ranger_runs.record` | `transcript`, `ocr_pages`, `observations` | Replaces the materialized run JSON with derived transcript text, OCR rows, and normalized observations; raw audio and image payloads are not separately persisted by this project |
| Observation graph write | FalkorDB graph `ranger` | `Mission`, `Platoon`, `Soldier`, `Task`, `Observation` nodes and relationships | Uses `MERGE` on canonical IDs, sets observation note/rating/timestamp, and links `Soldier -> Platoon -> Mission`, `Soldier -> Observation`, and `Observation -> Task` |
| Recommendation drafting and policy | Postgres `ranger_runs.record` | `recommendations[]`, run `status` | Stores draft `ScenarioRecommendation` records with policy decisions; allowed items become `pending`, blocked items become `blocked`, and the run moves to `pending_approval` |
| Processing completes or fails | Postgres `ranger_audit_events` | `run_status_updated` or `run_failed` | Appends immutable lifecycle events with final processing status or error text |
| `POST /v1/recommendations/{id}/decision` approve/reject | Postgres `ranger_runs.record` | Matching recommendation status | Updates the materialized run JSON to `approved` or `rejected`; blocked recommendations cannot be approved |
| Approved recommendation emit | FalkorDB graph `ranger` | `Recommendation` node | Uses `MERGE`, sets target soldier, rationale, risk level, and fairness score, then links `Recommendation -> Soldier` with `TARGETS` |
| Recommendation decision | Postgres `ranger_audit_events` | `recommendation_decision_recorded` | Appends an immutable approval/rejection audit event with actor and recommendation ID |
| Recommendation decision | Postgres `ranger_outbox_events` | `recommendation.approved` or `recommendation.rejected` | Appends a pending integration event containing recommendation ID, decision status, and target soldier ID |
| `POST /v1/outbox/{event_id}/published` | Postgres `ranger_outbox_events` | Outbox event `status` | Mutates only `status`, from `pending` to `published`, after a consumer confirms it applied the event |
| Direct `PgVectorStore.upsert` adapter use | Postgres `ranger_vector_documents` with pgvector | Vector document keyed by `(namespace, document_id)` | Upserts retrievable text, metadata, and embedding; this adapter is implemented, but the ingest workflow does not yet call it automatically |

System 1 does not delete shared records. It does not write System 2 trajectory
profiles or System 3 lessons-learned records. It currently reads health from
the configured infrastructure and writes only the records listed above.

## System 1 Current Tables And Keys

When Postgres is configured, `PostgresRunStore` creates these operational
tables:

| Table | Primary key | Mutability | Purpose |
|---|---|---|---|
| `ranger_runs` | `run_id` | Mutable materialized state | Current run status, ingest envelope, transcript, OCR rows, observations, KG write summary, recommendation records, and errors |
| `ranger_audit_events` | `event_id` | Append-only | Run lifecycle and instructor decision events |
| `ranger_outbox_events` | `event_id` | Append-only except `status` | Integration events for other systems to consume |
| `ranger_vector_documents` | `(namespace, document_id)` | Upsert by namespace/document ID | Semantic documents and embeddings for pgvector retrieval |

`ranger_runs.record` is the current materialized state. Other systems should
prefer audit/outbox/update-ledger records when they need a historical sequence
of changes.

## Canonical IDs

These IDs are shared across all apps and must be treated as stable foreign keys:

| ID | Meaning | Owning source |
|---|---|---|
| `soldier_id` | Individual Ranger/student identifier | Roster or System 2 |
| `instructor_id` | Instructor/operator identifier | Auth/roster source |
| `platoon_id` | Platoon identifier | Roster or mission planning source |
| `patrol_id` | Patrol identifier | Mission planning source |
| `mission_id` | Mission/scenario identifier | Mission planning source |
| `phase` | Ranger School phase: `Benning`, `Mountain`, `Florida` | Mission context |
| `task_code` | Doctrine or OR task code, for example `MV-2` | Doctrine seed data |
| `observation_id` | Atomic observed fact | Producing ingest service |
| `recommendation_id` | Agent-proposed scenario modification | System 1 |
| `run_id` | Processing run identifier | Producing service |
| `event_id` | Audit or outbox event identifier | Producing service |
| `version_id` | Immutable update/snapshot version | Producing service |

The frontend should not send full person records. It should send IDs such as
`soldier_id`, `platoon_id`, `mission_id`, or `run_id`. Services must resolve
the current details from shared stores.

## Data Locator Format

Every persisted record that can be cited by an agent should have a stable
locator:

```text
store://namespace/entity_type/entity_id[#field]
```

Examples:

```text
falkor://ranger/Soldier/Jones
falkor://ranger/Observation/obs-123#note
postgres://ranger_runs/run-123#record.recommendations[0]
postgres://ranger_audit_events/event-123
pgvector://doctrine/TC-3-21-76-MV-2
```

Use these locators in provenance fields so another app can retrieve the exact
record later.

## Required Provenance On Agentic Outputs

Any output generated or transformed by an agent must include evidence bindings.
This applies to recommendations, summaries, lessons, trajectory updates,
fairness scores, risk labels, and dashboard metrics.

Minimum provenance shape:

```json
{
  "output_id": "rec-123",
  "output_type": "scenario_recommendation",
  "target_ids": {
    "soldier_id": "Jones",
    "mission_id": "mission-1",
    "platoon_id": "plt-1"
  },
  "evidence_refs": [
    {
      "ref": "falkor://ranger/Observation/obs-123#note",
      "role": "primary_observation"
    },
    {
      "ref": "pgvector://doctrine/TC-3-21-76-MV-2",
      "role": "doctrine"
    }
  ],
  "model_context_refs": [
    "postgres://ranger_runs/run-123#record.observations"
  ],
  "policy_refs": [
    "postgres://ranger_audit_events/event-456"
  ],
  "created_by": "system-1",
  "created_at_utc": "2026-05-02T00:00:00Z"
}
```

Rules:

- `target_ids` is required when the output refers to an individual, platoon,
  patrol, mission, or task.
- `evidence_refs` must be non-empty for any recommendation or model-generated
  assessment.
- `doctrine_refs` remain human-readable, but `evidence_refs` are the machine
  retrievable source bindings.
- Never cite raw audio, raw images, or unredacted PII. Cite derived transcript,
  OCR row, observation, or redacted document records.

## Store Ownership

### FalkorDB

FalkorDB stores relationship truth:

```cypher
(Soldier {soldier_id})-[:MEMBER_OF]->(Platoon {platoon_id})
(Platoon)-[:PART_OF]->(Mission {mission_id})
(Soldier)-[:HAS_OBSERVATION]->(Observation {observation_id})
(Observation)-[:ON_TASK]->(Task {task_code})
(Recommendation {recommendation_id})-[:TARGETS]->(Soldier)
(Recommendation)-[:DERIVED_FROM]->(Observation)
(Recommendation)-[:CITES]->(Task)
```

All apps may read the graph. Writes must use `MERGE` on canonical IDs. A service
must not delete another service's nodes or relationships.

Current System 1 graph writes:

- `write_observations` implements `Mission`, `Platoon`, `Soldier`, `Task`, and
  `Observation` node merges plus the observation relationships shown above.
- `write_recommendation` currently implements `Recommendation` merge and
  `Recommendation-[:TARGETS]->Soldier` only. `DERIVED_FROM` and `CITES` are
  target-contract relationships and should be added when recommendation
  `evidence_refs` are implemented.

### Postgres

Postgres stores durable operational truth:

- current run records
- immutable audit events
- outbox events
- update ledger entries
- materialized current profile snapshots
- source snapshots for drift checks

Audit/update/outbox tables are append-only except for outbox publication status.

### pgvector

pgvector stores retrievable text and embeddings. Each document must include
metadata:

```json
{
  "source_service": "system-1",
  "source_ref": "falkor://ranger/Observation/obs-123",
  "entity_ids": {
    "soldier_id": "Jones",
    "mission_id": "mission-1",
    "task_code": "MV-2"
  },
  "version_id": "ver-123"
}
```

Namespaces should be explicit:

| Namespace | Contents |
|---|---|
| `doctrine` | Doctrine chunks and task definitions |
| `observations` | Redacted observations and extracted notes |
| `lessons` | Lessons learned from System 3 |
| `trajectory` | System 2 longitudinal summaries |
| `recommendations` | Approved/rejected recommendation rationale |

### Redis

Redis is not a source of truth. Use it for:

- `ranger:run-lease:{run_id}`
- `ranger:checkpoint:{thread_id}`
- `ranger:rate:{actor_id}`
- `ranger:cache:{entity_type}:{entity_id}`

Every key must have a TTL unless it is a documented checkpoint key.

## Cross-App Lookup Flow

Frontend sends only an ID:

```json
{
  "soldier_id": "Jones",
  "mission_id": "mission-1"
}
```

Service handling flow:

1. Resolve current person/unit/mission graph context from FalkorDB.
2. Resolve durable facts, snapshots, and prior decisions from Postgres.
3. Retrieve semantic context from pgvector using canonical IDs in metadata.
4. Use Redis only for cache/lease/checkpoint acceleration.
5. Produce output with `target_ids` and `evidence_refs`.
6. Write an append-only audit/update event before exposing the output.

## Update And Drift Tracking

All service updates must be stored separately from current materialized state.
This lets the three apps compare expected state against actual state and detect
drift.

Recommended update ledger shape:

```json
{
  "version_id": "ver-123",
  "entity_type": "soldier_profile",
  "entity_id": "Jones",
  "source_service": "system-2",
  "operation": "upsert",
  "base_version_id": "ver-100",
  "patch": {
    "readiness_score": 82.5
  },
  "source_refs": [
    "postgres://ranger_runs/run-123",
    "falkor://ranger/Observation/obs-123"
  ],
  "content_hash_before": "sha256:...",
  "content_hash_after": "sha256:...",
  "created_at_utc": "2026-05-02T00:00:00Z"
}
```

Rules:

- Store new facts as updates first, then update materialized current state.
- Keep `base_version_id` when modifying a known previous state.
- Store `content_hash_before` and `content_hash_after` for drift detection.
- If an app sees a different `content_hash_before` than expected, it must write
  a `drift_detected` audit event and avoid silent overwrite.
- Agentic outputs must cite the version IDs they used.

Current System 1 status:

- Run state changes are materialized in `ranger_runs.record`.
- Lifecycle and instructor decisions are stored separately in
  `ranger_audit_events`.
- Cross-system decision notifications are stored separately in
  `ranger_outbox_events`.
- A dedicated `ranger_update_ledger` table and content-hash drift helper are
  not implemented yet.

## Drift Detection Responsibilities

| Service | Drift checks |
|---|---|
| System 1 | Recommendation evidence still exists; target soldier still in roster/mission context; policy decision still matches latest safety/fairness inputs |
| System 2 | Trajectory profile was generated from expected observation/recommendation versions |
| System 3 | Lesson references still map to valid mission/task/person IDs |
| Frontend | Displays stale indicators when current version differs from viewed version |

## Outbox Contract

Services that need to react to another service's writes should poll outbox
events rather than scraping tables.

System 1 currently exposes:

```text
GET /v1/outbox
POST /v1/outbox/{event_id}/published
```

Outbox payloads must include:

- `event_id`
- `event_type`
- `aggregate_id`
- `run_id`
- `payload`
- `status`
- `timestamp_utc`

Consumers should mark events published only after successfully applying their
own update ledger entry.

## Required Validation

Every app should have tests or evals proving:

- IDs are canonical and no local replacement IDs are minted.
- Generated outputs include non-empty `evidence_refs`.
- Updates are append-only and preserve `base_version_id`.
- Drift is detected when `content_hash_before` does not match.
- Frontend workflows can request details by ID only.
- Outbox consumers are idempotent.

## Current Gaps To Implement

- Add `evidence_refs` and `target_ids` fields to System 1 output contracts.
- Use those evidence refs to write `Recommendation-[:DERIVED_FROM]->Observation`
  and `Recommendation-[:CITES]->Task` graph edges.
- Add a Postgres update ledger table and drift detection helper.
- Add pgvector ingestion for doctrine, observations, lessons, and trajectory
  summaries.
- Add cross-system endpoints for soldier/mission detail lookup by canonical ID.
- Add frontend stale-version indicators.

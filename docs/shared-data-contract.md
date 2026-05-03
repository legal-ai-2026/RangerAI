# Shared Data Contract - All Systems

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
| `GET` | `/v1/readyz` | Frontend, operators, other services | Check critical runtime readiness | critical dependency status and provider/model configuration |
| `POST` | `/v1/ingest` | Frontend or instructor workflow | Start a new processing run | `run_id`, initial `status=accepted`, original `ingest` |
| `GET` | `/v1/runs/{run_id}` | Frontend, System 2, System 3 | Fetch canonical System 1 run state | `observations`, `recommendations`, `kg_write_summary`, `errors` |
| `GET` | `/v1/dashboard/runs/{run_id}` | Frontend | Fetch presentation-neutral summary | per-soldier GO/NOGO counts, readiness score, active recommendations |
| `GET` | `/v1/missions/{mission_id}/state` | Frontend | Fetch compact mission-command projection | latest run, platoon, phase, readiness, observations, recommendation counts, source refs |
| `GET` | `/v1/entities/soldiers/{soldier_id}` | Frontend, System 2, System 3 | Fetch System 1's read-only projection for a soldier ID | runs, observations, recommendation records, and update refs tied to that soldier |
| `GET` | `/v1/entities/missions/{mission_id}` | Frontend, System 2, System 3 | Fetch System 1's read-only projection for a mission ID | runs, soldier IDs, observations, recommendation records, and update refs tied to that mission |
| `GET` | `/v1/soldiers/{soldier_id}/performance` | Soldier-facing app or frontend | Fetch self-service performance guidance | aggregate metrics, recent task ratings, and instructor-approved recommendations only |
| `GET` | `/v1/soldier/{soldier_id}/training-trajectory` | System 2, frontend drilldowns | Fetch a read-only System 1 trajectory projection | task trends, development-edge counts, source refs, and update refs |
| `GET` | `/v1/runs/{run_id}/audit` | Frontend, System 2, System 3 | Inspect lifecycle and decision events | immutable `AuditEvent[]` ordered by timestamp |
| `GET` | `/v1/recommendations/recent` | Frontend | Fetch recent recommendation queue | `EntityRecommendation[]`, optionally filtered by `mission_id` or `status` |
| `GET` | `/v1/recommendations/{recommendation_id}` | Frontend, System 2, System 3 | Fetch one recommendation with run/policy context | `EntityRecommendation` |
| `POST` | `/v1/recommendations/{recommendation_id}/decision` | Frontend/instructor workflow | Approve, edit-approve, or reject a recommendation | `ApprovalResponse` with final status |
| `GET` | `/v1/graph/subgraph` | Frontend, System 2, System 3 | Fetch a relationship projection around run, mission, soldier, or recent state | `GraphSubgraph` nodes and edges with source refs |
| `GET` | `/v1/outbox` | System 2, System 3, integration workers | Poll pending System 1 decision events | `OutboxEvent[]` |
| `GET` | `/v1/update-ledger` | System 2, System 3, integration workers | Poll append-only System 1 observation, recommendation, and lesson-signal updates | `UpdateLedgerEntry[]` filtered by optional `entity_type` and `entity_id` |
| `POST` | `/v1/outbox/{event_id}/published` | System 2, System 3, integration workers | Mark a consumed event as published | `event_id`, `status=published` |
| `POST` | `/v1/lessons-learned` | System 3 | Record an idempotent lesson signal receipt | `LessonsLearnedReceipt` with `status=accepted` or `duplicate` |

The frontend may call the decision endpoint by `recommendation_id` only. This
service resolves the owning `run_id` from its run store.

The trajectory endpoint is read-only and does not create a System 2 trajectory
profile. The lessons endpoint records only a System 1 receipt/update reference;
System 3 remains the source of truth for the lesson record itself.

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
| `target_ids` | object | Canonical soldier, platoon, mission, patrol, and task IDs used by the output |
| `evidence_refs` | object array | Machine-readable locators for observations, doctrine, and context used by the output |
| `model_context_refs` | string array | Run/context locators passed into the recommendation step |
| `policy_refs` | string array | Policy/audit locators used by the decision path |
| `intervention_id` | string or null | Curated intervention-library ID used to generate the recommendation |
| `learning_objective` | string or null | Competency-oriented objective for the scenario modification |
| `score_breakdown` | object or null | Transparent candidate score components before policy filtering |
| `created_by` | string | Producing service, currently `system-1` |
| `created_at_utc` | datetime | Recommendation creation timestamp |

`score_breakdown` contains `learning_delta`, `doctrinal_fit`,
`instructor_utility`, `novelty_bonus`, `safety_risk`, `fatigue_overload`,
`fairness_penalty`, `repetition_penalty`, and `total`. The score ranks library
candidates before deterministic policy and instructor review; it is not an
approval signal.

`RecommendationRecord` wraps a recommendation with:

- `policy.allowed`
- `policy.reasons`
- `policy.fairness_score`
- `status`

`SoldierEntityProjection` is a read-only System 1 projection by canonical
soldier ID. It includes:

- matching `runs`
- matching `observations`
- matching `recommendations` with policy and status
- `update_refs` pointing at `ranger_update_ledger` entries

`MissionEntityProjection` is the same pattern by canonical mission ID. It also
includes the set of `soldier_ids` System 1 observed or targeted in that mission.

`SoldierPerformanceReport` is for soldier-facing display. It intentionally does
not expose raw instructor audio, image payloads, OCR pages, or unapproved
recommendation drafts. It returns aggregate performance counts, recent task
ratings, `pending_review_count`, `blocked_recommendation_count`, and only
`approved_recommendations`.

`SoldierTrainingTrajectory` is a System 2-facing projection. It returns:

- `run_count`, `observation_count`, `approved_recommendation_count`
- `go_rate` and `readiness_score`
- task-level summaries with GO/NOGO/UNCERTAIN counts and simple trend labels
- development-edge counts by recommendation status
- recent observation points with source refs
- `source_refs` and `update_refs` for downstream provenance checks

`LessonsLearnedSignal` is accepted from System 3. It must include `lesson_id`,
`summary`, and at least one canonical linkage: `mission_id`, `soldier_ids`,
`task_codes`, or `recommendation_ids`. `POST /v1/lessons-learned` is
idempotent on `lesson_id`; duplicate requests return `status=duplicate` and do
not append another update ledger entry.

`OutboxEvent.payload` currently contains:

```json
{
  "recommendation_id": "rec-123",
  "status": "approved",
  "target_soldier_id": "Jones",
  "target_ids": {
    "soldier_id": "Jones",
    "platoon_id": "plt-1",
    "mission_id": "mission-1",
    "task_code": "MV-2"
  },
  "evidence_refs": [
    {
      "ref": "falkor://ranger/Observation/obs-123#note",
      "role": "primary_observation"
    }
  ],
  "model_context_refs": [
    "postgres://ranger_runs/run-123#record.observations"
  ],
  "policy_refs": []
}
```

Systems 2 and 3 should resolve the full run, observation, recommendation, and
audit context by reading `GET /v1/runs/{run_id}` and
`GET /v1/runs/{run_id}/audit` after receiving an outbox event.
`OutboxEvent.trace_id` carries the frontend or API-generated correlation id
from the decision request when available.

`UpdateLedgerEntry` records append-only System 1 updates:

| Field | Type | Meaning |
|---|---|---|
| `version_id` | string | Immutable update version ID |
| `entity_type` | string | `observation`, `recommendation`, or `lesson_signal` for current System 1 writes |
| `entity_id` | string | `observation_id` or `recommendation_id` |
| `source_service` | string | Producing or source service, for example `system-1` or `system-3` |
| `operation` | enum | `create`, `observe`, `approve`, `reject`, or future update operation |
| `trace_id` | string or null | Frontend or API-generated correlation id for support and audit stitching |
| `base_version_id` | string or null | Prior version when known |
| `patch` | object | JSON patch/projection payload for the update |
| `source_refs` | string array | Source locators used by the update |
| `content_hash_before` | string or null | Hash before update when known |
| `content_hash_after` | string | Hash of the normalized patch |
| `created_at_utc` | datetime | Update timestamp |

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
| New observation recorded | Postgres `ranger_update_ledger` | `observation` update | Appends an immutable `observe` event with observation patch, source refs, and content hash |
| Observation graph write | FalkorDB graph `ranger` | `Mission`, `Platoon`, `Soldier`, `Task`, `Observation` nodes and relationships | Uses `MERGE` on canonical IDs, sets observation note/rating/timestamp, and links `Soldier -> Platoon -> Mission`, `Soldier -> Observation`, and `Observation -> Task` |
| Recommendation drafting and policy | Postgres `ranger_runs.record` | `recommendations[]`, run `status` | Stores draft `ScenarioRecommendation` records with policy decisions; allowed items become `pending`, blocked items become `blocked`, and the run moves to `pending_approval` |
| Processing completes or fails | Postgres `ranger_audit_events` | `run_status_updated` or `run_failed` | Appends immutable lifecycle events with final processing status or error text |
| `POST /v1/recommendations/{id}/decision` approve/edit/reject | Postgres `ranger_runs.record` | Matching recommendation status and optional edited recommendation | Updates the materialized run JSON to `approved` or `rejected`; edited approvals rerun policy and blocked recommendations cannot be approved |
| Recommendation decision | Postgres `ranger_update_ledger` | `recommendation` update | Appends an immutable `approve` or `reject` event with target IDs, evidence refs, source refs, and content hash |
| Approved recommendation emit | FalkorDB graph `ranger` | `Recommendation` node and provenance edges | Uses `MERGE`, sets target soldier, rationale, risk level, and fairness score, then links `Recommendation -> Soldier` with `TARGETS`, `Recommendation -> Observation` with `DERIVED_FROM`, and `Recommendation -> Task` with `CITES` when provenance is present |
| Recommendation decision | Postgres `ranger_audit_events` | `recommendation_decision_recorded` | Appends an immutable approval/rejection audit event with actor and recommendation ID |
| Recommendation decision | Postgres `ranger_outbox_events` | `recommendation.approved` or `recommendation.rejected` | Appends a pending integration event containing recommendation ID, decision status, and target soldier ID |
| `POST /v1/outbox/{event_id}/published` | Postgres `ranger_outbox_events` | Outbox event `status` | Mutates only `status`, from `pending` to `published`, after a consumer confirms it applied the event |
| `POST /v1/lessons-learned` | Postgres `ranger_lesson_signals` and `ranger_update_ledger` | Lesson signal receipt keyed by `lesson_id` | Inserts the receipt only once per `lesson_id`; first receipt appends a `lesson_signal` update with System 3 as `source_service`, duplicates return without another update |
| Direct `PgVectorStore.upsert` adapter use | Postgres `ranger_vector_documents` with pgvector | Vector document keyed by `(namespace, document_id)` | Upserts retrievable text, metadata, and embedding; this adapter is implemented, but the ingest workflow does not yet call it automatically |

System 1 does not delete shared records. It does not write System 2 trajectory
profiles or System 3 lessons-learned records. `ranger_lesson_signals` stores
only System 1 receipt metadata and provenance so downstream consumers can tell
that a System 3 lesson was seen.

## System 1 Current Tables And Keys

When Postgres is configured, `PostgresRunStore` creates these operational
tables:

| Table | Primary key | Mutability | Purpose |
|---|---|---|---|
| `ranger_runs` | `run_id` | Mutable materialized state | Current run status, ingest envelope, transcript, OCR rows, observations, KG write summary, recommendation records, and errors |
| `ranger_audit_events` | `event_id` | Append-only | Run lifecycle and instructor decision events |
| `ranger_outbox_events` | `event_id` | Append-only except `status` | Integration events for other systems to consume |
| `ranger_update_ledger` | `version_id` | Append-only | Observation and recommendation update history with source refs and content hashes |
| `ranger_lesson_signals` | `lesson_id` | Insert-only/idempotent | System 3 lesson-signal receipts used as System 1 integration context |
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
- `write_recommendation` implements `Recommendation` merge,
  `Recommendation-[:TARGETS]->Soldier`,
  `Recommendation-[:DERIVED_FROM]->Observation`, and
  `Recommendation-[:CITES]->Task` when recommendation provenance contains
  observation refs and task IDs.

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
- Observation and recommendation updates are stored separately in
  `ranger_update_ledger`.
- Cross-record drift comparison jobs are not implemented yet.

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
GET /v1/update-ledger
GET /v1/entities/soldiers/{soldier_id}
GET /v1/entities/missions/{mission_id}
GET /v1/soldiers/{soldier_id}/performance
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

## Shared Validation Requirements

Every app should have tests or evals proving:

- IDs are canonical and no local replacement IDs are minted.
- Generated outputs include non-empty `evidence_refs`.
- Updates are append-only and preserve `base_version_id`.
- Drift is detected when `content_hash_before` does not match.
- Frontend workflows can request details by ID only.
- Outbox consumers are idempotent.

## System 1 Current Gaps To Implement

- Add a drift detection comparison helper over `ranger_update_ledger`.
- Add pgvector ingestion for doctrine, observations, lessons, and trajectory
  summaries.
- Add frontend stale-version indicators.

## System 2 Project Profile

System 2 is the roster recommendation and career forecast service. Other apps
should treat it as the owner of roster recommendation runs, scoring trace,
approval records, fairness audits, and roster-decision context. It consumes
soldier, mission, training, outcome, policy, and graph facts; it does not own
the canonical soldier record, final deployment authority, or System 1 training
observations.

| Area | Detail |
|---|---|
| Repository | `c2d2-teambuilder-model` |
| Runtime service | FastAPI app `system2.api:app` |
| Default local URL | `http://127.0.0.1:8000` |
| Primary responsibility | score soldiers against mission role slots and produce human-approved roster recommendations |
| Primary stable run ID | `run_id` generated by System 2 |
| Owns | agent runs, recommendations, approvals, audit records, retrieval context, graph facts accepted through its APIs |
| Consumes | soldiers, missions, role slots, training observations, deployment outcomes, policy/SOP context, graph facts |

System 2 can produce a primary roster, second-choice roster, confidence and
model-disagreement explanations, fairness/proxy audits, trace metadata,
approval records, context chunks in pgvector, and derived FalkorDB facts.

System 2 must not mutate soldier-of-record data, System 1 training
observations, System 3 deployment outcomes, or final assignment authority.

### System 2 API For Other Projects

| Method | Path | Purpose | Mutates shared state |
|---|---|---|---|
| `GET` | `/v1/healthz` | Check service and backend selection | no |
| `POST` | `/v1/score` | Direct roster scoring | audit only |
| `POST` | `/v1/agent-runs` | Agentic recommendation workflow | yes |
| `GET` | `/v1/agent-runs/{run_id}` | Fetch run and recommendation | no |
| `POST` | `/v1/agent-runs/{run_id}/approval` | Record approve/reject decision | yes |
| `POST` | `/v1/context/chunks` | Ingest retrievable policy/SOP/context chunks | yes |
| `POST` | `/v1/graph/facts` | Ingest derived relationship facts | yes |
| `POST` | `/admin/disable` | Disable scoring | audit/control only |
| `POST` | `/admin/enable` | Re-enable scoring | audit/control only |

Current direct score input:

```json
{
  "mission_id": "raid-tonight",
  "candidate_count": 80,
  "seed": 42,
  "candidates": [],
  "roles": []
}
```

Target integrated input:

```json
{
  "mission_id": "raid-tonight",
  "candidate_pool_id": "pool-2026-05-02-a"
}
```

System 2 should resolve mission requirements, role slots, candidate soldiers,
current soldier projections, training projections, outcome history,
policy/SOP context, and graph relationships from shared stores.

### System 2 Data Inputs

Candidate fields currently consumed by the scorer:

| Field | Usage |
|---|---|
| `soldier_id` | trace and roster identity |
| `unit_id` | pooled unit effect; hashed in audit |
| `mos` | role qualification and pooled MOS effect; hashed in audit |
| `age_years` | proxy/fairness context; not directly scored |
| `two_mile_run_sec` | physical readiness |
| `self_efficacy_score` | readiness signal |
| `peer_rating_z` | leadership/cohesion signal |
| `home_unit_ranger_density` | experience/context signal |
| `acft_score` | readiness signal and hard role gate |
| `operational_readiness` | mission success signal |
| `prior_missions` | experience and uncertainty |
| `medical_risk` | safety/risk penalty |
| `landing_asymmetry_score` | safety/risk penalty |
| `hip_extension_power_w` | adapter expansion field |
| `change_of_direction_index` | local pattern signal |
| `fatigue_index` | safety/risk penalty |
| `sandbox_score` | simulation performance signal |
| `milestones` | readiness terms |
| `competencies` | role-specific fit terms |
| `protected_race` | fairness audit only |
| `protected_gender` | fairness audit only |

Role fields currently consumed:

| Field | Usage |
|---|---|
| `slot_id` | assignment slot identity |
| `role` | role-specific scoring weights |
| `required_mos` | hard disqualifier when set |
| `min_acft` | hard disqualifier |

### System 2 Outputs

Direct score output:

```json
{
  "mission_id": "raid-tonight",
  "roster": [],
  "second_choice_roster": [],
  "fairness_audit": {},
  "career_forecast": {},
  "trace": {}
}
```

Each roster item contains `slot_id`, `role`, `soldier_id`, `fit_score`,
`p_success_tabpfn`, `p_success_bayes_mean`, `model_disagreement`,
`p_success_bayes_ci`, `confidence`, `narrative`, `key_strengths`,
`risk_factors`, and `second_choice_id`.

Agent run output contains `run_id`, `status`, original request, ordered agent
steps, recommendation payload, approval payload if decided, error if failed,
and timestamps. Other apps should treat `run_id` as the durable handle for
System 2 decisions.

### System 2 Recommendation Logic

System 2 scores every `(soldier, role)` pair.

1. Compute deterministic role fit from physical readiness, operational
   readiness, experience, simulation score, milestones, and competencies.
2. Penalize medical risk, landing asymmetry, and fatigue.
3. Exclude protected attributes from scoring and assignment.
4. Compute TabPFN-compatible and Bayes-compatible success probabilities.
5. Blend probabilities and calculate model disagreement.
6. Set confidence from disagreement: high below `0.10`, medium from `0.10` to
   `0.25`, low above `0.25`.
7. Apply hard gates for required MOS and minimum ACFT.
8. Solve primary roster with Hungarian assignment.
9. Solve second-choice roster by blocking primary `(soldier, role)` pairs.
10. Generate fairness audit, narrative, career forecast, trace metadata, and
    audit records.

### System 2 Store Ownership

| Store | System 2 objects |
|---|---|
| Postgres | `system2_agent_runs`, `system2_audit_log`, `system2_context_chunks` |
| pgvector | `system2_context_chunks.embedding` |
| Redis | `system2:agent-run:{run_id}:status`, `system2:agent-run:{run_id}:lock` |
| FalkorDB | derived graph facts accepted through `/v1/graph/facts` |

System 2 should append `entity_update_events` for approvals, context ingest,
graph ingest, and kill-switch changes. It should write `decision_snapshots`
when direct recommendations or approved agent recommendations are returned.

### System 2 Write Summary

| Operation | Endpoint | Postgres writes | pgvector writes | FalkorDB writes | Redis writes |
|---|---|---|---|---|---|
| Direct score | `POST /v1/score` | `system2_audit_log`; target `decision_snapshots` | none | none | none |
| Agent run create | `POST /v1/agent-runs` | `system2_agent_runs`, `system2_audit_log`; target `decision_snapshots` | none | none | run status and lock |
| Agent approval | `POST /v1/agent-runs/{run_id}/approval` | `system2_agent_runs`, `system2_audit_log`, target `entity_update_events` | none | optional assignment facts after approval | status update |
| Context ingest | `POST /v1/context/chunks` | `system2_context_chunks`, target `entity_update_events` | chunk embedding | none | optional cache invalidation |
| Graph fact ingest | `POST /v1/graph/facts` | target `entity_update_events` | none | graph facts in `system2` graph | optional cache invalidation |
| Kill switch | `/admin/disable`, `/admin/enable` | `system2_audit_log`, target `entity_update_events` | none | none | none |

### System 2 Cross-App Requirements

System 1 should provide training observations, competency/milestone
projections, simulation outcomes, skill/qualification graph facts, and source
hashes. System 3 should provide prior assignments, deployment outcomes, mission
outcome observations, outcome graph facts, and source hashes. The frontend
should provide `mission_id`, future `candidate_pool_id`, `run_id` for detail
fetches, and approval/rejection decisions with approver ID and rationale.

Other projects can rely on System 2 for durable `run_id`, recommendation
status, selected roster, second-choice roster, confidence/disagreement fields,
fairness audit payload, trace metadata, audit records, approval details,
accepted context chunk records, and accepted graph facts. A recommendation is
not final until the run is completed and an approval payload exists.

Current System 2 gaps:

- `entity_update_events` writes for approval, context ingest, graph ingest, and
  kill-switch changes.
- `decision_snapshots` writes for direct recommendations and approved agent
  recommendations.
- richer `source_refs` on each recommendation and agent step.
- ID-only request resolution through `candidate_pool_id`.

## System 3 Project Profile

System 3 is the combat deployment intelligence and COA planning service. Other
apps should treat it as the owner of planning runs, COAs, rehearsal scenarios,
COA approval responses, agent outputs, and future drift findings. It consumes
canonical mission, person, unit, ROE, intel, enemy-pattern, terrain, and
assignment records; it does not own canonical person, unit, intel, ROE,
terrain, or enemy-pattern records.

| Area | Detail |
|---|---|
| App ID | `system3` |
| Package | `spire_deploy` |
| API service | `spire_deploy.gateway.main:app` |
| API title | `System 3 Operations Gateway` |
| Authentication | `X-API-Key` must match `SYSTEM3_API_KEY` |
| Primary responsibility | generate, validate, store, retrieve, and review COA planning runs |
| Current canonical-read implementation | `SyntheticRepository`, backed by `data/synthetic/` |
| Current checkpoint store | Redis when `REDIS_URL` is set; otherwise in-memory |
| Primary stable run ID | `planning-run-{uuid}` |
| Primary derived output IDs | `coa-{missionId}-{seq}`, `scenario-{missionId}-{seq}` |

System 3 should not receive assembled mission context from the frontend. The
frontend should pass `missionId`, `runId`, `coaId`, or person/soldier IDs and
let System 3 resolve context from shared storage.

### System 3 API For Other Projects

| Method | Path | Purpose | Caller sends IDs only |
|---|---|---|---|
| `GET` | `/v1/healthz` | service health | no |
| `GET` | `/v1/healthz/infrastructure` | redacted datastore reachability | no |
| `GET` | `/v1/mission-context/{missionId}` | resolved mission context projection | `missionId` |
| `POST` | `/v1/mission/assignment` | local assignment update | `missionId`, `soldierIds` |
| `POST` | `/v1/coa/propose` | create a planning run | `missionId` |
| `GET` | `/v1/coa/proposals/{runId}` | fetch stored planning run | `runId` |
| `POST` | `/v1/coa/proposals/{runId}/approval` | approve or reject a COA | `runId`, `coaId` |

COA proposal request:

```json
{
  "missionId": "mission-compound-iron",
  "requestedBy": "operator.id",
  "includeRehearsalScenarios": true
}
```

COA approval request:

```json
{
  "coaId": "coa-mission-compound-iron-1",
  "reviewedBy": "commander.id",
  "decision": "Approve",
  "justification": "Approved after review."
}
```

### System 3 Derived Outputs

System 3 creates these derived records:

- `PlanningRun`
- `AgentOutput`
- `COA`
- `RehearsalScenario`
- `COAApprovalResponse`
- future `DriftFinding`

COA proposal responses include `runId`, `missionId`, `producedAt`, `coas`,
`rehearsalScenarios`, and `agentTrace`. COAs include `coaId`, `missionId`,
`title`, `insertionOption`, `summary`, `keyDecisionMoments`,
`citedRoeRuleIds`, `citedIntelReportIds`, `riskScore`, `roeStatus`, `state`,
and `classificationMarking`.

Rehearsal scenarios include `scenarioId`, `missionId`, `title`,
`insertionOption`, `narrative`, `keyDecisionMoments`, `citedPatternIds`,
`citedRoeRuleIds`, and `classificationMarking`.

### System 3 Agent Trace Contract

Every `agentTrace` entry currently contains `agentName`, `status`, `summary`,
`inputRefs`, `outputRefs`, `warnings`, and `metrics`.

Current agent names:

- `MissionContextAgent`
- `EnemyPatternMinerAgent`
- `ScenarioGeneratorAgent`
- `COACriticAgent`
- `PlanningGuardAgent`

Current trace references are object-level only:

```json
{
  "sourceType": "Mission",
  "sourceId": "mission-compound-iron",
  "note": null
}
```

When System 3 moves to shared Postgres persistence, each trace reference must
also include `recordVersion`, `recordHash`, and optionally `fieldPath`.

### System 3 Current Recommendation Logic

Current logic is deterministic and reproducible:

1. `EnemyPatternMinerAgent` sorts mission-linked enemy patterns by confidence.
2. `ScenarioGeneratorAgent` creates three insertion variants:
   `GroundConvoy`, `AirAssault`, and `DismountedPatrol`.
3. `COACriticAgent` creates one COA per scenario.
4. `riskScore` is deterministic by scenario index.
5. `roeStatus` is derived from keywords in the COA summary.
6. `PlanningGuardAgent` blocks invalid outputs before response persistence.

Guardrail checks:

- COA/scenario classification must not exceed mission ceiling.
- COAs cannot be pre-approved before human review.
- COAs must cite mission ROE.
- COAs must cite only mission-context intel.
- Scenarios must cite enemy patterns.
- Scenarios must cite mission ROE.

Other projects can validate System 3 recommendations by checking the five-agent
trace order, non-empty ROE/intel/pattern citations, `PlanningGuardAgent.status`
equals `Succeeded`, and allowed `classificationMarking`.

### System 3 Read Set

For `POST /v1/coa/propose`, System 3 reads:

- `Mission` by `missionId`
- linked `Unit`
- linked `Soldier` records
- linked `MissionPhase` records
- linked `ROERule` records
- linked `IntelReport` records
- linked `EnemyPattern` records
- linked `TerrainFeature` records

### System 3 Store Ownership

| Store | Current behavior | Target behavior |
|---|---|---|
| Postgres | health-checkable, not yet in recommendation path | canonical records, planning runs, agent outputs, COAs, scenarios, approvals, drift findings |
| Redis | `system3:planning-run:{runId}` checkpoint with 86400 second TTL | active-run checkpoint/cache only |
| FalkorDB | configured and health-checkable | relationship traversal and derived output edges |
| pgvector | configured and health-checkable | versioned retrieval rows for intel, patterns, lessons, and source text |

Redis planning-run values serialize `PlanningState` and include context,
selected patterns, scenarios, COAs, and agent trace. Redis is not canonical;
durable consumers should use the API now and Postgres `planning_runs` /
`agent_outputs` once persistence is added.

### System 3 Endpoint Data Effects

| Endpoint | Reads | Writes now | Target persistent writes |
|---|---|---|---|
| `GET /v1/mission-context/{missionId}` | mission context by ID | none | none |
| `POST /v1/mission/assignment` | mission and soldier IDs | in-memory mission assignment only | `app_update_events`, `mission_assignments`, FalkorDB assignment edge version |
| `POST /v1/coa/propose` | mission context, ROE, intel, patterns, terrain | Redis or memory planning run | `planning_runs`, `agent_outputs`, `coas`, `rehearsal_scenarios`, graph edges, output versions |
| `GET /v1/coa/proposals/{runId}` | stored planning run | none | none |
| `POST /v1/coa/proposals/{runId}/approval` | stored planning run and COA | updates stored COA state in Redis or memory | `app_update_events`, `coas.state`, `agent_output_versions`, approval event |
| `GET /v1/healthz/infrastructure` | env config and TCP reachability | none | none |

### System 3 Graph Contract

Recommended node labels:

- `Person`
- `Soldier`
- `Unit`
- `Mission`
- `MissionPhase`
- `IntelReport`
- `EnemyPattern`
- `EnemyEntity`
- `ROERule`
- `TerrainFeature`
- `COA`
- `RehearsalScenario`
- `LessonLearned`
- `AgentRun`
- `AgentOutput`

Recommended relationship types:

- `ASSIGNED_TO`
- `MEMBER_OF`
- `PART_OF_MISSION`
- `MISSION_USES_ROE`
- `MISSION_REFERENCES_INTEL`
- `MISSION_HAS_TERRAIN`
- `PATTERN_CITES_INTEL`
- `PATTERN_INVOLVES_ENEMY`
- `COA_CITES_ROE`
- `COA_CITES_INTEL`
- `COA_BASED_ON_SCENARIO`
- `SCENARIO_CITES_PATTERN`
- `AGENT_USED_SOURCE`
- `AGENT_PRODUCED_OUTPUT`
- `LESSON_DERIVED_FROM`

Graph nodes should store stable ID, object type, classification marking, and
current canonical version. Large descriptions and full payloads stay in
Postgres/object storage, not FalkorDB.

### System 3 Drift Triggers

System 3 outputs should be checked for drift when any of these source records
change:

- mission objective
- mission classification ceiling
- mission phases
- mission assignments
- ROE rules
- cited intel reports
- selected enemy patterns
- terrain features used by a scenario
- graph relationships connecting the mission to source data
- embeddings for cited intel/pattern/lesson text

Required drift response:

1. Preserve the original run.
2. Write a `drift_findings` row.
3. Mark affected outputs as `NeedsReview`.
4. Generate a new planning run when updated recommendations are needed.
5. Do not mutate old agent outputs to appear current.

Current System 3 gaps:

- Persist canonical records, planning runs, update ledgers, graph
  relationships, and embeddings to shared infrastructure.
- Enrich trace refs with record versions and hashes.
- Treat Redis planning state as cache only once Postgres persistence exists.
- Mark approvals stale when cited source records drift.

## Cross-System Durable Tables

The three apps should converge on these shared durable tables or equivalent
migrations. Current-state tables are projections. Append-only tables explain
how projections and agent outputs changed.

### Current Projections

| Table | Primary IDs | Purpose |
|---|---|---|
| `soldiers_current` | `soldier_id` | current non-sensitive soldier profile and restricted protected attributes for fairness paths |
| `missions_current` | `mission_id` | current mission details |
| `role_slots_current` | `mission_id`, `slot_id` | current mission role slots and hard constraints |
| `training_observations_current` | `soldier_id`, observation/version ID | current training and skill projection |
| `deployment_outcomes_current` | `soldier_id`, `mission_id` | current deployment/outcome projection |
| `people` / `person_aliases` | `person_id`, aliases | canonical person and cross-system identity mapping |
| `units` | `unit_id` | canonical unit records |
| `mission_assignments` | `mission_id`, assignment/version ID | current mission assignment projection |
| `intel_reports` | `intel_id` | canonical intel records |
| `roe_rules` | `roe_id` | canonical ROE records |
| `terrain_features` | `terrain_id` | canonical terrain records |

### Append-Only Updates

`entity_update_events` or `app_update_events` should contain:

| Field | Meaning |
|---|---|
| `event_id` / `updateId` | primary key |
| `entity_type` / `objectType` | updated object type |
| `entity_id` / `objectId` | canonical object ID |
| `source_app` / `appId` | `system1`, `system2`, `system3`, `frontend`, or `operator` |
| `source_record_id` | upstream record ID |
| `operation` | `create`, `correct`, `observe`, `approve`, `reject`, `supersede`, or app-specific operation |
| `event_payload` | JSONB update payload |
| `previous_source_hash` / `beforeHash` | hash before update |
| `new_source_hash` / `afterHash` | hash after update |
| `observed_at` / `occurredAt` | when fact was true or action occurred |
| `recorded_at` | when update was written |
| `actor_id` | user or service identity |
| `reason` | human-readable rationale |

Never update append-only event rows in place.

### Decision And Drift Tables

| Table | Purpose |
|---|---|
| `decision_snapshots` | normalized request hash, input source hashes, output hash, and fairness hash for System 2-style recommendations |
| `agent_output_versions` | source versions and output hashes for System 3-style agent outputs |
| `drift_observations` / `drift_findings` | comparisons between baseline and current source/output versions |

Drift exists when an output was generated from one version of data but current
canonical data, graph relationships, policy/context chunks, or embedding text
hashes have changed. Preserve old outputs and create new runs for material
changes.

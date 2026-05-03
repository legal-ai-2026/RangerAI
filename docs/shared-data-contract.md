# Shared Data Contract

This document is for all services that share the graph-stack infrastructure:
System 1 Ranger adversarial training agent, System 2 training trajectory service,
System 3 lessons-learned service, and the frontend.

The rule is simple: the frontend sends canonical IDs, services resolve details
from shared stores, and every agentic output must cite the exact data records
that caused it.

## Shared Infrastructure

| Store | Shared role | Write rule |
|---|---|---|
| Postgres | Durable run state, audit events, outbox events, snapshots, update ledgers | Append-only for audits/updates; mutable only for current materialized state |
| pgvector | Semantic retrieval over doctrine, observations, lessons, and summaries | Documents are upserted by namespace and `document_id`; source metadata is mandatory |
| FalkorDB | Canonical relationship graph for people, units, missions, tasks, observations, recommendations | Use idempotent `MERGE`; never mint substitute person or mission IDs |
| Redis | Leases, locks, checkpoints, rate counters, short-lived cache | TTL required for coordination keys |

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
- Add a Postgres update ledger table and drift detection helper.
- Add pgvector ingestion for doctrine, observations, lessons, and trajectory
  summaries.
- Add cross-system endpoints for soldier/mission detail lookup by canonical ID.
- Add frontend stale-version indicators.

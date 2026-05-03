# Frontend Integration Guide

This document explains how a frontend should integrate with the System 1 Ranger
adversarial training agent. The service is an API-only backend. It does not own
the frontend, canonical roster profile, or canonical mission profile.

Use this guide with:

- `GET /docs` or `GET /openapi.json` from the running FastAPI app.
- `docs/shared-data-contract.md` for cross-application data ownership rules.
- `docs/architecture.md` for workflow and guardrail context.

## Integration Principles

1. Send IDs, not assembled operational context.
   The frontend should send canonical `soldier_id`, `instructor_id`,
   `platoon_id`, `mission_id`, `patrol_id`, and `task_code` values. Other
   services or shared stores own full roster and mission truth.

2. Treat System 1 outputs as training decision support.
   Recommendations are drafts until an instructor approves or rejects them.
   The frontend must not display pending recommendations as approved training
   intent.

3. Keep instructor and soldier views separate.
   Instructor views may show pending recommendation cards and evidence. Soldier
   views should use `GET /v1/soldiers/{soldier_id}/performance`, which exposes
   aggregate performance and approved guidance only.

4. Preserve provenance.
   Display evidence and source counts where useful. Store `run_id`,
   `recommendation_id`, and source refs in frontend state so decisions can be
   audited and refreshed.

5. Do not expose raw ingest data unnecessarily.
   Raw audio/image payloads are accepted on ingest, but frontend screens should
   prefer derived transcript, OCR rows, observations, and recommendation cards.

## Base URL And Discovery

Local default:

```text
http://127.0.0.1:8001
```

Useful discovery endpoints:

```text
GET /docs
GET /openapi.json
GET /v1/healthz
```

`/v1/healthz` reports whether provider keys and infrastructure adapters are
configured. The response is safe to show in an operator view; it does not print
secrets.

Current backend status:

- No frontend code is hosted by this repository.
- No auth middleware is implemented in this service yet.
- Do not expose the API directly on a public network. Put it behind the
  frontend gateway, VPN, or another authenticated internal boundary.
- CORS is not configured in this service. In development, proxy through the
  frontend dev server or API gateway. Add explicit CORS middleware only as a
  deliberate backend change.

## Primary Frontend Flows

### 1. Instructor Ingest And Review Loop

Use this for the main hackathon demo and operational instructor workflow.

```text
POST /v1/ingest
  -> returns 202 and an accepted RunRecord
poll GET /v1/runs/{run_id}
  -> accepted | processing | pending_approval | completed | failed
GET /v1/dashboard/runs/{run_id}
  -> platoon and per-soldier dashboard projection
POST /v1/recommendations/{recommendation_id}/decision
  -> approve or reject each pending recommendation
GET /v1/runs/{run_id}/audit
  -> lifecycle and decision audit trail
```

Recommended polling:

- Poll `GET /v1/runs/{run_id}` every 1-2 seconds during local demos.
- Stop polling when status is `pending_approval`, `completed`, or `failed`.
- If status is `failed`, show `errors[]` and offer operator retry through a new
  ingest rather than mutating the failed run.

### 2. Instructor Dashboard

Use this once a run exists.

```text
GET /v1/dashboard/runs/{run_id}
```

This response is presentation-neutral and already groups:

- total observations
- pending, blocked, and approved recommendation counts
- platoon readiness score
- per-soldier GO/NOGO/UNCERTAIN counts
- per-soldier metrics
- active recommendations

Recommended UI panels:

- Run status and mission metadata.
- Platoon readiness summary.
- Soldier table with `go_rate`, `readiness_score`, and metric status.
- Recommendation queue grouped by `pending`, `blocked`, `approved`, `rejected`.
- Evidence/provenance drawer for selected recommendations.

### 3. Recommendation Approval

Decision endpoint:

```text
POST /v1/recommendations/{recommendation_id}/decision
```

Request:

```json
{
  "decision": "approve"
}
```

or:

```json
{
  "decision": "reject"
}
```

Response:

```json
{
  "run_id": "run-123",
  "recommendation_id": "rec-123",
  "status": "approved"
}
```

Frontend behavior:

- Disable the approve button for recommendations with `status="blocked"`.
- Show `policy.reasons[]` for blocked items.
- Refresh `GET /v1/runs/{run_id}` after every decision.
- Treat `409` as a conflict, usually because the run is being processed or a
  blocked recommendation was incorrectly approved.
- Edited recommendations are not implemented. Sending
  `edited_recommendation` currently returns `501`.

### 4. Soldier-Facing Performance View

Use this for a student-facing or self-service view.

```text
GET /v1/soldiers/{soldier_id}/performance?limit=100
```

This endpoint intentionally does not expose:

- raw audio
- raw images
- OCR pages
- full observation notes
- pending recommendation draft text

It does expose:

- aggregate observation counts
- `go_rate`
- `readiness_score`
- metric cards
- development edges
- approved recommendations
- pending and blocked counts
- recent observation digests with source refs

Recommended soldier view:

- Summary metrics at top.
- Recent task ratings without instructor note text.
- Approved development guidance.
- A small "pending instructor review" count when applicable.

### 5. Cross-System Entity Views

Use these when the frontend has only a canonical ID and needs to show what
System 1 knows about it.

```text
GET /v1/entities/soldiers/{soldier_id}?limit=100
GET /v1/entities/missions/{mission_id}?limit=100
```

These are System 1 projections, not canonical profile objects.

Soldier projection includes:

- matching runs
- matching observations
- matching recommendation records
- update ledger refs

Mission projection includes:

- matching runs
- observed/targeted soldier IDs
- observations
- recommendation records
- update ledger refs

Use these views for:

- mission detail pages
- soldier detail pages
- cross-app drilldowns from System 2 or System 3
- provenance and history drawers

### 6. Integration Worker Outbox

Use this for service-to-service consumers, not normal UI screens.

```text
GET /v1/outbox?limit=100
POST /v1/outbox/{event_id}/published
```

Outbox events are created after recommendation decisions. Consumers should mark
events published only after downstream processing succeeds.

### 7. Update Ledger

Use this for history, provenance, and drift-aware refresh logic.

```text
GET /v1/update-ledger
GET /v1/update-ledger?entity_type=observation
GET /v1/update-ledger?entity_type=recommendation&entity_id=rec-123
```

The update ledger is append-only. It is useful for:

- showing historical changes
- cross-service synchronization
- stale-version indicators
- traceability panels

## Request And Response Shapes

### IngestEnvelope

`POST /v1/ingest`

Required:

- `instructor_id`
- `platoon_id`
- `mission_id`
- `phase`: `Benning`, `Mountain`, or `Florida`
- `geo.lat`
- `geo.lon`
- `geo.grid_mgrs`
- at least one of `audio_b64`, `image_b64[]`, or `free_text`

Example:

```json
{
  "instructor_id": "ri-1",
  "platoon_id": "plt-1",
  "mission_id": "mission-mountain-01",
  "phase": "Mountain",
  "timestamp_utc": "2026-05-03T18:30:00Z",
  "geo": {
    "lat": 35.0,
    "lon": -83.0,
    "grid_mgrs": "17S"
  },
  "free_text": "Jones blew Phase Line Bird. Smith asleep at 0300. Garcia textbook ambush rehearsal.",
  "audio_b64": null,
  "image_b64": []
}
```

Frontend file handling:

- Convert audio/image files to raw base64 strings.
- Do not include data URL prefixes such as `data:image/jpeg;base64,`.
- Keep uploads small for the demo; large files should be compressed or rejected
  by the frontend before submission.
- Show a local warning if no evidence source is attached.

### RunRecord

Returned by:

```text
POST /v1/ingest
GET /v1/runs/{run_id}
```

Important fields:

- `run_id`
- `status`
- `ingest`
- `transcript`
- `ocr_pages`
- `observations`
- `kg_write_summary`
- `recommendations`
- `errors`

Run statuses:

| Status | Frontend treatment |
|---|---|
| `accepted` | Show queued/accepted state. |
| `processing` | Show spinner/progress state. |
| `pending_approval` | Show recommendation review queue. |
| `completed` | Show final run state and decisions. |
| `failed` | Show errors and operator action. |

### Observation

Important fields:

- `observation_id`
- `soldier_id`
- `task_code`
- `note`
- `rating`: `GO`, `NOGO`, or `UNCERTAIN`
- `timestamp_utc`
- `source`: `audio`, `image`, `free_text`, or `synthetic`

Instructor views may show observation notes. Soldier-facing views should use
`SoldierPerformanceReport.recent_observations`, which omits note text.

### RecommendationRecord

Each run contains `recommendations[]`.

Important fields:

- `recommendation`
- `policy`
- `status`: `pending`, `approved`, `rejected`, or `blocked`

Frontend display rules:

- `pending`: instructor can approve or reject.
- `approved`: may be displayed as instructor-approved training intent.
- `rejected`: show as historical decision only.
- `blocked`: show policy reasons; do not allow approval.

### ScenarioRecommendation

Important fields:

- `recommendation_id`
- `target_soldier_id`
- `target_ids`
- `rationale`
- `development_edge`
- `learning_objective`
- `intervention_id`
- `proposed_modification`
- `doctrine_refs`
- `safety_checks`
- `estimated_duration_min`
- `requires_resources`
- `risk_level`
- `fairness_score`
- `score_breakdown`
- `evidence_refs`
- `model_context_refs`
- `policy_refs`
- `created_at_utc`

`score_breakdown` is for transparency, not approval. The instructor decision
and `RecommendationRecord.status` determine whether a recommendation is
actionable.

Score fields:

| Field | Meaning |
|---|---|
| `learning_delta` | Expected training value for the observed development edge. |
| `doctrinal_fit` | Fit to task/doctrine mapping. |
| `instructor_utility` | Practical usefulness for cadre. |
| `novelty_bonus` | Preference for varied scenario pressure. |
| `safety_risk` | Penalty for physical/safety concern. |
| `fatigue_overload` | Penalty for sleep/cold/load overreach. |
| `fairness_penalty` | Penalty for over-targeting one student. |
| `repetition_penalty` | Penalty for repeated same task/soldier pattern. |
| `total` | Ranked candidate score before policy and approval. |

### PolicyDecision

Important fields:

- `allowed`
- `reasons`
- `fairness_score`

Display policy reasons prominently when `allowed=false`.

### DashboardRunSummary

Recommended for the instructor dashboard.

Important fields:

- `run_id`
- `mission_id`
- `platoon_id`
- `phase`
- `status`
- `total_observations`
- `pending_recommendations`
- `blocked_recommendations`
- `approved_recommendations`
- `platoon_readiness_score`
- `soldiers[]`

### SoldierPerformanceReport

Recommended for soldier-facing view.

Important fields:

- `soldier_id`
- `observations_count`
- `go_count`
- `nogo_count`
- `uncertain_count`
- `go_rate`
- `readiness_score`
- `metrics`
- `development_edges`
- `approved_recommendations`
- `pending_review_count`
- `blocked_recommendation_count`
- `recent_observations`

## UI State Model

Recommended client-side state:

```text
currentRunId
currentRunRecord
currentDashboardSummary
selectedRecommendationId
selectedSoldierId
selectedMissionId
pollingStatus
lastError
```

Recommended derived UI state:

```text
pendingRecommendations = run.recommendations where status == "pending"
blockedRecommendations = run.recommendations where status == "blocked"
decidedRecommendations = run.recommendations where status in ["approved", "rejected"]
canApprove = recommendation.status == "pending" && recommendation.policy.allowed
canReject = recommendation.status == "pending"
```

Polling state:

```text
idle -> submitted -> polling -> pending_approval -> deciding -> completed
                               -> failed
```

## Error Handling

Expected errors:

| Status | Common cause | Frontend response |
|---|---|---|
| `404` | Run, recommendation, soldier projection, or mission projection not found. | Show not-found state and allow navigation back. |
| `409` | Run is processing or invalid approval conflict. | Refresh run state and disable stale controls. |
| `422` | Invalid payload or query limit. | Show validation messages near the form. |
| `501` | Edited recommendation submitted. | Hide edit submit or show "not implemented". |
| `5xx` | Provider, infrastructure, or unexpected backend failure. | Show operator-facing failure, inspect run errors if available. |

Limit query parameters:

- `limit` must be between `1` and `500` on list/projection endpoints.
- Default is `100`.

## Security And Data Handling

Frontend must:

- Avoid storing raw audio/images in browser state longer than needed.
- Avoid logging raw ingest payloads to analytics.
- Redact or avoid displaying sensitive free-text where not needed.
- Keep soldier-facing screens on `SoldierPerformanceReport`.
- Never treat pending recommendations as approved actions.
- Require an authenticated instructor/operator identity at the gateway layer.

Backend currently:

- Scrubs common PII patterns before LLM calls.
- Validates inbound contracts with Pydantic.
- Blocks unsafe or invalid recommendations before approval.
- Requires instructor decision before emit.
- Records audit, outbox, and update-ledger events.

## Recommended Screens

### Instructor Ingest Screen

Inputs:

- instructor ID
- platoon ID
- mission ID
- phase
- location/grid
- audio upload
- OR booklet image upload
- free-text notes

Actions:

- submit ingest
- clear local evidence
- navigate to run page

### Run Review Screen

Data:

- `GET /v1/runs/{run_id}`
- `GET /v1/dashboard/runs/{run_id}`
- `GET /v1/runs/{run_id}/audit`

Panels:

- status timeline
- extracted observations
- recommendation cards
- policy and safety panel
- score breakdown panel
- evidence refs
- approve/reject controls

### Mission Screen

Data:

- `GET /v1/entities/missions/{mission_id}`

Panels:

- related runs
- observed soldier IDs
- mission observations
- mission recommendations
- update refs

### Soldier Instructor Detail Screen

Data:

- `GET /v1/entities/soldiers/{soldier_id}`

Panels:

- runs touching the soldier
- observations with notes
- recommendation history
- update refs

### Soldier Self-Service Screen

Data:

- `GET /v1/soldiers/{soldier_id}/performance`

Panels:

- aggregate performance counts
- readiness and metric cards
- recent ratings
- approved recommendations
- pending review count

## Local Demo Sequence

1. Start API:

   ```bash
   uv run uvicorn src.api.main:app --reload --port 8001
   ```

2. Check health:

   ```text
   GET http://127.0.0.1:8001/v1/healthz
   ```

3. Submit ingest:

   ```text
   POST http://127.0.0.1:8001/v1/ingest
   ```

4. Poll run:

   ```text
   GET http://127.0.0.1:8001/v1/runs/{run_id}
   ```

5. Load dashboard:

   ```text
   GET http://127.0.0.1:8001/v1/dashboard/runs/{run_id}
   ```

6. Approve or reject each pending recommendation:

   ```text
   POST http://127.0.0.1:8001/v1/recommendations/{recommendation_id}/decision
   ```

7. Refresh dashboard and audit:

   ```text
   GET http://127.0.0.1:8001/v1/dashboard/runs/{run_id}
   GET http://127.0.0.1:8001/v1/runs/{run_id}/audit
   ```

## Integration Checklist

- The frontend uses only `/v1` paths.
- The frontend can submit an `IngestEnvelope` with at least one evidence source.
- The frontend polls run status until terminal or reviewable state.
- Pending recommendation cards show score breakdown, policy reasons, evidence,
  doctrine refs, and safety checks.
- Approve/reject calls use `recommendation_id`.
- Blocked recommendations cannot be approved in UI.
- Soldier-facing views use `/v1/soldiers/{soldier_id}/performance`.
- Cross-app drilldowns use entity projection endpoints.
- Raw audio/image payloads are not persisted in frontend state after upload.
- Errors `404`, `409`, `422`, and `501` have explicit UI states.
- The deployment path provides authentication and network protection before
  exposing the API to users.

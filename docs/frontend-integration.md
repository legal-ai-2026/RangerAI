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

6. Treat calibration as feedback capture, not automation.
   `calibration_support` tells the instructor which cues to watch and when to
   capture feedback. It must not auto-approve recommendations or hide the
   instructor decision gate.

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
GET /v1/readyz
```

`/v1/healthz` reports whether provider keys and infrastructure adapters are
configured. The response is safe to show in an operator view; it does not print
secrets.
`/v1/readyz` reports critical runtime readiness and returns the same model
configuration summary without exposing keys.

If `SYSTEM1_API_KEY` is configured, include it on operational requests:

```text
X-API-Key: <configured value>
```

The frontend may also send a stable trace id per user action:

```text
X-Trace-Id: <frontend-generated-correlation-id>
```

If the header is omitted, the API generates one and returns it as
`X-Trace-Id`. Persist the value with the run or decision UI state so support
logs, audit events, outbox events, and update-ledger entries can be correlated.

Current backend status:

- No frontend code is hosted by this repository.
- Optional API-key middleware is available. Set `SYSTEM1_API_KEY` and send
  `X-API-Key` on operational `/v1` requests. `/v1/healthz` and `/v1/readyz`
  remain unauthenticated for readiness checks.
- Optional CORS allowlisting is available. Set `CORS_ALLOW_ORIGINS` to a
  comma-separated list such as `http://localhost:3000,https://frontend.example`.
- Do not expose the API directly on a public network. Put it behind the
  frontend gateway, VPN, or another authenticated internal boundary.

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
GET /v1/missions/{mission_id}/state
  -> compact mission-command projection
GET /v1/missions/{mission_id}/team-calibration-profile
  -> mission/platoon cue-outcome summary derived from approved feedback
GET /v1/recommendations/recent?mission_id={mission_id}
  -> recommendation queue / recent cards
GET /v1/recommendations/{recommendation_id}
  -> one recommendation with policy and run context
GET /v1/graph/subgraph?mission_id={mission_id}
  -> graph projection for drilldowns
POST /v1/recommendations/{recommendation_id}/decision
  -> approve or reject each pending recommendation
POST /v1/recommendations/{recommendation_id}/feedback
  -> record post-inject calibration feedback for approved recommendations
GET /v1/soldiers/{soldier_id}/calibration-profile
  -> cue/outcome calibration summary
GET /v1/runs/{run_id}/audit
  -> lifecycle and decision audit trail
```

Recommended polling:

- Poll `GET /v1/runs/{run_id}` every 1-2 seconds during local demos.
- Stop polling when status is `pending_approval`, `completed`, or `failed`.
- If status is `failed`, show `errors[]` and offer operator retry through a new
  ingest rather than mutating the failed run.

### 2. Instructor Dashboard

Use this as the primary instructor workspace once a run exists. The dashboard
should feel like an operations console: dense, scan-friendly, and built around
review, approval, and feedback capture rather than a marketing-style landing
page.

```text
GET /v1/dashboard/runs/{run_id}
```

Load the dashboard as a small bundle, keyed by `run_id` and `mission_id`:

```text
GET /v1/runs/{run_id}
GET /v1/dashboard/runs/{run_id}
GET /v1/missions/{mission_id}/state
GET /v1/recommendations/recent?mission_id={mission_id}
GET /v1/runs/{run_id}/audit
```

Add these lazily when a panel is opened:

```text
GET /v1/recommendations/{recommendation_id}
GET /v1/graph/subgraph?mission_id={mission_id}
GET /v1/missions/{mission_id}/team-calibration-profile
GET /v1/soldier/{soldier_id}/training-trajectory
GET /v1/soldiers/{soldier_id}/calibration-profile
GET /v1/entities/soldiers/{soldier_id}
GET /v1/entities/missions/{mission_id}
```

`DashboardRunSummary` is presentation-neutral and already groups:

- total observations
- pending, blocked, and approved recommendation counts
- platoon readiness score
- per-soldier GO/NOGO/UNCERTAIN counts
- per-soldier metrics
- active recommendations

Recommended layout:

- **Command bar:** mission ID, platoon ID, phase, run status, trace ID, health
  badge, refresh action, and last updated time.
- **Left rail:** run selector, mission selector, soldier selector, and compact
  filters for `pending`, `blocked`, `approved`, `rejected`, and `needs review`.
- **Main top band:** readiness score, observation count, recommendation count,
  approval progress, and policy-block count.
- **Main center:** tabs for `Review Queue`, `Soldiers`, `Mission`, `Graph`,
  `Calibration`, and `Audit`.
- **Right inspector:** details for the selected recommendation, soldier,
  observation, graph node, or audit event.

Use tabs or segmented controls for dashboard modes. Use compact tables,
timelines, progress bars, badges, and status chips rather than large decorative
cards. Keep individual repeated items as cards only when they represent one
recommendation, soldier, run, or audit event.

Core dashboard panels:

| Panel | Primary endpoint | Purpose | Required UI behavior |
|---|---|---|---|
| Run status timeline | `GET /v1/runs/{run_id}` and `/audit` | Show accepted, processing, review, decision, feedback, and outbox events. | Highlight failed states and link each decision to `recommendation_id`. |
| Readiness overview | `GET /v1/dashboard/runs/{run_id}` | Show platoon readiness and per-soldier readiness at a glance. | Sort by review need by default; allow sorting by readiness, GO rate, NOGO count. |
| Observation board | `GET /v1/runs/{run_id}` | Show extracted observations and source confidence. | Mark `UNCERTAIN` and low-confidence rows; link source refs in the inspector. |
| Review queue | run recommendations plus `/recommendations/recent` | Drive approve/reject workflow. | Group by pending, blocked, approved, rejected; blocked items cannot be approved. |
| Decision quality | recommendation detail | Explain evidence quality, reliance risk, safety/fairness margin, and value of information. | Require rationale and acknowledgements when backend marks review requirements as required. |
| Policy and safety | recommendation detail | Make guardrails inspectable. | Show `policy.reasons[]`, `policy_refs[]`, safety checks, risk controls, and risk level. |
| Calibration cues | recommendation detail and calibration endpoints | Turn approved injects into feedback-rich events. | Show cue tags to watch before approval and feedback capture after approval. |
| Team calibration | `/team-calibration-profile` | Show mission/platoon cue-outcome patterns. | Treat as read-only judgement support, not a grade or leaderboard. |
| Soldier trajectory | `/training-trajectory` and `/calibration-profile` | Show longitudinal context for selected soldier. | Show task trends, development edges, recent points, and calibration trend. |
| Graph context | `/graph/subgraph` | Show relationship/provenance drilldown. | Use node selection to filter inspector details; never require graph view for approval. |
| Outbox/update state | `/outbox` and `/update-ledger` | Show integration readiness and stale-state indicators. | Display pending outbox count and ledger refs in an operator drawer. |

Review queue card contents:

- target soldier and task code
- status and policy badge
- development edge and intervention ID
- evidence summary and `why_now`
- doctrine refs and evidence refs count
- decision-quality rating and reliance-risk indicator
- score breakdown summary
- safety checks and risk controls
- calibration cue tags to watch
- required review acknowledgements
- approve, reject, edit, and record-feedback actions where allowed

Soldier grid contents:

- soldier ID
- observation count
- GO/NOGO/UNCERTAIN counts
- GO rate
- readiness score
- active recommendation count
- blocked recommendation count
- strongest development edges
- calibration outcome trend
- quick link to trajectory and entity projection

Mission tab contents:

- mission state summary from `/v1/missions/{mission_id}/state`
- run count and latest run
- observed soldier IDs
- total observations and recommendation counts
- team calibration trend
- mission recommendation list
- graph and update-ledger drilldowns

Calibration tab contents:

- team cue profiles grouped by cue tag
- development-edge outcome patterns
- member summaries for drilldown links
- pending recommendations that need calibration feedback
- approved recommendations missing post-inject feedback
- feedback form for selected approved recommendation

Evidence inspector contents:

- original observation note or digest where appropriate for instructor view
- transcript/OCR-derived rows if present
- evidence refs, model context refs, policy refs, source refs
- doctrine refs and doctrine chunk excerpts when available
- weather and terrain source refs when present
- audit events and update-ledger refs tied to the selected item

Interaction rules:

- Selecting a soldier filters observations, recommendations, trajectory, and
  calibration panels to that soldier.
- Selecting a recommendation opens the right inspector and preserves
  `run_id`, `recommendation_id`, `target_soldier_id`, and `trace_id` in state.
- Approve and reject actions optimistically disable controls, then refresh
  `GET /v1/runs/{run_id}`, `/dashboard/runs/{run_id}`, and recommendation
  detail after the response.
- Edited approvals must show an edit summary and require
  `decision_rationale`.
- Feedback submission must refresh recommendation detail, soldier calibration,
  team calibration, and update ledger.
- A stale-state badge should appear when a selected item's `update_refs`
  changed after it was opened.
- The dashboard must preserve filters and selected IDs across refreshes.

Loading and empty states:

- `accepted` or `processing`: show run timeline, ingestion metadata, and a
  processing skeleton for observations/recommendations.
- `pending_approval`: open the `Review Queue` tab automatically.
- `completed`: default to `Mission` or `Audit` if no pending items remain.
- `failed`: show `errors[]`, trace ID, and a retry-new-ingest action.
- No calibration signals: show `insufficient_data` with a prompt to capture
  feedback after approved recommendations.
- No graph nodes: show a provenance-unavailable state and keep review controls
  available from typed recommendation data.

Dashboard acceptance criteria:

- An instructor can submit ingest, watch status, review observations, approve or
  reject every pending recommendation, record feedback for approved
  recommendations, and inspect audit history without leaving the dashboard.
- No pending recommendation is visually represented as an approved action.
- Blocked recommendations show policy reasons and have no approve action.
- Required review acknowledgements and rationale are enforced in the UI before
  submit.
- Soldier-facing surfaces never show raw ingest payloads or pending
  recommendation draft text.
- The dashboard remains useful when graph, calibration, weather, or terrain
  context is absent or degraded.

Implementation note: this repository must stay API-only. Put the actual
frontend code in the frontend application; keep this repository limited to typed
API responses, docs, and backend tests.

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

If the recommendation has required `review_requirements`, or if the instructor
approves an edited recommendation, send:

```json
{
  "decision": "approve",
  "decision_rationale": "Instructor reviewed the cited source evidence and accepts the risk controls.",
  "acknowledged_review_requirements": ["medium_risk_ack"]
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
- Show `decision_frame`, `decision_quality`, `value_of_information`, and
  `review_requirements` for pending items.
- Require a rationale input and acknowledgement checkboxes for every
  `review_requirements[]` item with `required_for_approval=true`.
- Refresh `GET /v1/runs/{run_id}` after every decision.
- Treat `409` as a conflict, usually because the run is being processed or a
  blocked or edited recommendation failed policy.
- To approve an instructor edit, send `decision="approve"` with a full
  `edited_recommendation` object whose `recommendation_id` matches the path ID.
  The backend preserves provenance fields from the original draft when the edit
  omits them, marks `created_by="instructor"`, reruns policy, and only emits the
  approved edited object. Edited approvals must include `decision_rationale`.
- Do not send `edited_recommendation` with `decision="reject"`; validation
  rejects that combination.

### 4. Calibration Feedback

Use this after an approved recommendation has been executed, cancelled, or
observed during the training event. The endpoint rejects feedback for pending,
blocked, or rejected recommendations.

```text
POST /v1/recommendations/{recommendation_id}/feedback
```

Request:

```json
{
  "signal_id": "cal-123",
  "recommendation_id": "rec-123",
  "run_id": "run-123",
  "instructor_id": "ri-1",
  "outcome": "improved",
  "cue_tags": ["communication_timing"],
  "observed_learning_signal": "Jones delivered a concise SITREP without prompting.",
  "confidence": 0.85,
  "notes": "The cue was visible at the next covered halt.",
  "evidence_refs": [
    {
      "ref": "postgres://ranger_runs/run-123#record.recommendations",
      "role": "source_recommendation"
    }
  ],
  "occurred_at_utc": "2026-05-03T19:10:00Z"
}
```

Response:

```json
{
  "signal_id": "cal-123",
  "status": "accepted",
  "source_refs": [
    "postgres://ranger_calibration_signals/cal-123",
    "postgres://ranger_runs/run-123#record.recommendations"
  ]
}
```

Allowed `outcome` values:

- `improved`
- `no_change`
- `worsened`
- `unsafe_abort`
- `unclear`

Allowed `cue_tags` values:

- `communication_timing`
- `security_posture`
- `fire_control_timing`
- `fatigue_stress`
- `terrain_interaction`
- `team_coordination`
- `leadership_delegation`
- `source_uncertainty`

Frontend behavior:

- Show the feedback form only for `status="approved"` recommendations.
- Pre-fill `signal_id` client-side with a stable UUID so retry is idempotent.
- Pre-fill `recommendation_id`, `run_id`, and `instructor_id` from current UI state.
- Prefer cue tags from `recommendation.calibration_support.cue_tags_to_watch`.
- Require at least one cue tag and a short observed learning signal.
- Treat `status="duplicate"` as success for retry-safe UX.
- Treat `409` as stale or invalid state; refresh the recommendation before retrying.

Recommended UI placement:

- On approved recommendation cards: show a "Record feedback" action.
- In run audit/history: show captured calibration feedback as immutable history.
- In instructor detail views: show cue/outcome patterns from the calibration
  profile, not raw notes by default.

### 5. Soldier-Facing Performance View

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

### 6. Training Trajectory Projection

Use this for System 2 drilldowns or an instructor-facing longitudinal view. It
is read-only and does not create or modify a System 2 trajectory profile.

```text
GET /v1/soldier/{soldier_id}/training-trajectory?limit=100
```

It exposes:

- run and observation counts
- `go_rate` and `readiness_score`
- task-level GO/NOGO/UNCERTAIN summaries
- simple task trend labels
- development-edge counts by recommendation status
- calibration profile summary for cue/outcome feedback
- recent observation points with source refs
- update refs for drift or stale-state checks

The embedded `calibration_profile` is a compact summary. Use the full
calibration profile endpoint when the UI needs cue-level detail.

### 7. Calibration Profile

Use this for instructor-facing longitudinal judgement support or System 2/3
drilldowns.

```text
GET /v1/soldiers/{soldier_id}/calibration-profile?limit=100
```

Important fields:

- `signal_count`
- `outcome_counts`
- `outcome_trend`: `insufficient_data`, `improving`, `mixed`, or `negative`
- `cue_profiles[]`
- `intervention_profiles[]`
- `source_refs`
- `update_refs`

Frontend behavior:

- Display `outcome_trend` as a review aid, not a pass/fail label.
- Surface `negative` or `mixed` trends in instructor views before approval.
- Use cue profiles to guide what instructors should watch next.
- Do not show calibration notes in soldier-facing views unless explicitly
  approved by product policy.

### 8. Team Calibration Profile

Use this for instructor-facing mission/platoon cue-outcome analysis. It does
not add or replace military assessment categories; it aggregates existing
approved recommendation feedback.

```text
GET /v1/missions/{mission_id}/team-calibration-profile?limit=100
```

Important fields:

- `mission_id`
- `platoon_id`
- `run_count`
- `soldier_count`
- `signal_count`
- `outcome_counts`
- `outcome_trend`
- `cue_profiles[]`
- `development_edge_profiles[]`
- `member_summaries[]`
- `source_refs`
- `update_refs`

Frontend behavior:

- Show this only in instructor or staff views.
- Treat `outcome_trend` as a team calibration aid, not a grade.
- Use `cue_profiles[]` to show which team cues need observation.
- Use `member_summaries[]` for drilldown links, not as a leaderboard.
- Expect `signal_count=0` and `outcome_trend="insufficient_data"` when the
  mission exists but no post-inject feedback has been captured.

The compact `GET /v1/missions/{mission_id}/state` response also includes
`team_calibration_profile` for dashboard badges or stale-state checks.

### 9. Cross-System Entity Views

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

### 10. Integration Worker Outbox

Use this for service-to-service consumers, not normal UI screens.

```text
GET /v1/outbox?limit=100
POST /v1/outbox/{event_id}/published
```

Outbox events are created after recommendation decisions. Consumers should mark
events published only after downstream processing succeeds.

### 11. Update Ledger

Use this for history, provenance, and drift-aware refresh logic.

```text
GET /v1/update-ledger
GET /v1/update-ledger?entity_type=observation
GET /v1/update-ledger?entity_type=recommendation&entity_id=rec-123
GET /v1/update-ledger?entity_type=calibration_signal&entity_id=cal-123
```

The update ledger is append-only. It is useful for:

- showing historical changes
- cross-service synchronization
- stale-version indicators
- traceability panels

Calibration feedback creates update ledger entries with
`entity_type="calibration_signal"` only on first receipt. Duplicate feedback
requests return `status="duplicate"` and do not append a second ledger entry.

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
- `trace_id`
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
- `calibration_support`
- `created_by`
- `created_at_utc`

`score_breakdown` is for transparency, not approval. The instructor decision
and `RecommendationRecord.status` determine whether a recommendation is
actionable.

`decision_quality` and `value_of_information` are also transparency fields.
They should calibrate review, not auto-approve or auto-reject recommendations.

`calibration_support` is the frontend's cue prompt for post-inject feedback. It
should be shown on pending and approved recommendation cards when present.

Important `calibration_support` fields:

- `calibration_goal`
- `cue_tags_to_watch`
- `feedback_prompt`
- `prior_signal_count`
- `outcome_trend`
- `recommended_feedback_window`
- `source_refs`

If `outcome_trend` is `negative`, the backend may add a required
`calibration_history_review` item to `review_requirements`. The UI should show
the acknowledgement checkbox like any other required review item.

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

### SoldierTrainingTrajectory

Recommended for longitudinal instructor views and System 2 drilldowns.

Important fields:

- `soldier_id`
- `run_count`
- `observation_count`
- `approved_recommendation_count`
- `go_rate`
- `readiness_score`
- `task_summaries`
- `development_edges`
- `calibration_profile`
- `recent_points`
- `source_refs`
- `update_refs`

### SoldierCalibrationProfile

Recommended for instructor-facing cue/outcome analysis.

Important fields:

- `soldier_id`
- `signal_count`
- `outcome_counts`
- `outcome_trend`
- `cue_profiles`
- `intervention_profiles`
- `source_refs`
- `update_refs`

`cue_profiles[]` groups calibration feedback by cue tag. `intervention_profiles[]`
groups feedback by intervention. Both are deterministic summaries of instructor
feedback, not model-generated judgement.

### TeamCalibrationProfile

Recommended for mission/platoon instructor views.

Important fields:

- `mission_id`
- `platoon_id`
- `run_count`
- `soldier_count`
- `signal_count`
- `outcome_counts`
- `outcome_trend`
- `cue_profiles`
- `development_edge_profiles`
- `member_summaries`
- `source_refs`
- `update_refs`

This profile is derived from existing `CalibrationSignal` records tied to runs
in the mission. It must not be displayed as a formal assessment score or used
to change GO/NOGO/UNCERTAIN status.

### CalibrationSignal

Used by `POST /v1/recommendations/{recommendation_id}/feedback`.

Required fields:

- `recommendation_id`
- `run_id`
- `instructor_id`
- `outcome`
- `cue_tags`
- `observed_learning_signal`

Optional or caller-generated fields:

- `signal_id`
- `confidence`
- `notes`
- `evidence_refs`
- `occurred_at_utc`

The backend hydrates `target_soldier_id`, `task_code`, `development_edge`, and
`intervention_id` from the referenced recommendation. Frontends should not rely
on manually supplied values for those fields.

## UI State Model

Recommended client-side state:

```text
currentRunId
currentRunRecord
currentDashboardSummary
selectedRecommendationId
selectedSoldierId
selectedMissionId
selectedCalibrationProfile
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
canRecordFeedback = recommendation.status == "approved"
requiredReviewIds = recommendation.recommendation.review_requirements where required_for_approval
cueTagsToWatch = recommendation.recommendation.calibration_support.cue_tags_to_watch
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
| `409` | Run is processing, invalid approval conflict, or feedback was submitted for a non-approved recommendation. | Refresh run state and disable stale controls. |
| `422` | Invalid payload or query limit. | Show validation messages near the form. |
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
- Require an authenticated instructor/operator identity at the gateway layer or
  configure `SYSTEM1_API_KEY` for this service in protected deployments.

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

### Instructor Dashboard Screen

Data:

- `GET /v1/runs/{run_id}`
- `GET /v1/dashboard/runs/{run_id}`
- `GET /v1/missions/{mission_id}/state`
- `GET /v1/recommendations/recent?mission_id={mission_id}`
- `GET /v1/runs/{run_id}/audit`
- lazy detail calls for selected recommendations, soldiers, graph nodes,
  calibration profiles, outbox events, and update-ledger entries

Panels:

- command bar with mission, phase, run status, trace ID, health, refresh, and
  last-updated state
- left rail for run, mission, soldier, and recommendation-status filters
- readiness overview with platoon score, observation volume, approval progress,
  and policy-block count
- status timeline
- review queue grouped by pending, blocked, approved, and rejected
  recommendations
- soldier grid with GO/NOGO/UNCERTAIN counts, readiness, active
  recommendations, and calibration trend
- observation board with confidence and source refs
- policy, safety, decision-quality, and score-breakdown inspector
- calibration tab with cue prompts from `calibration_support`, feedback gaps,
  and team cue/outcome summaries
- mission tab with state, related runs, latest recommendations, graph drilldown,
  outbox state, and update-ledger state
- audit tab with lifecycle, decision, feedback, and integration events
- approve/reject controls for pending recommendations
- feedback controls for approved recommendations

### Mission Screen

Data:

- `GET /v1/entities/missions/{mission_id}`
- `GET /v1/missions/{mission_id}/state`
- `GET /v1/missions/{mission_id}/team-calibration-profile`

Panels:

- related runs
- observed soldier IDs
- mission observations
- mission recommendations
- team calibration cue/outcome summary
- update refs

### Soldier Instructor Detail Screen

Data:

- `GET /v1/entities/soldiers/{soldier_id}`
- `GET /v1/soldier/{soldier_id}/training-trajectory`
- `GET /v1/soldiers/{soldier_id}/calibration-profile`

Panels:

- runs touching the soldier
- observations with notes
- recommendation history
- task trends and development edges
- calibration cue/outcome summary
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
   uv run python tools/run_api.py --host 0.0.0.0 --port 8001 --reload
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
   GET http://127.0.0.1:8001/v1/missions/{mission_id}/state
   GET http://127.0.0.1:8001/v1/recommendations/recent?mission_id={mission_id}
   GET http://127.0.0.1:8001/v1/graph/subgraph?mission_id={mission_id}
   ```

6. Approve or reject each pending recommendation:

   ```text
   POST http://127.0.0.1:8001/v1/recommendations/{recommendation_id}/decision
   ```

7. Record feedback for approved recommendations:

   ```text
   POST http://127.0.0.1:8001/v1/recommendations/{recommendation_id}/feedback
   GET http://127.0.0.1:8001/v1/soldiers/{soldier_id}/calibration-profile
   GET http://127.0.0.1:8001/v1/missions/{mission_id}/team-calibration-profile
   ```

8. Refresh dashboard and audit:

   ```text
   GET http://127.0.0.1:8001/v1/dashboard/runs/{run_id}
   GET http://127.0.0.1:8001/v1/runs/{run_id}/audit
   ```

9. Run the synthetic HTTP smoke loop:

   ```bash
   uv run python tools/smoke_synthetic.py
   ```

## Integration Checklist

- The frontend uses only `/v1` paths.
- The frontend can submit an `IngestEnvelope` with at least one evidence source.
- The frontend polls run status until terminal or reviewable state.
- Pending recommendation cards show score breakdown, policy reasons, evidence,
  doctrine refs, safety checks, and calibration cue prompts.
- Approve/reject calls use `recommendation_id`.
- Recommendation details use `GET /v1/recommendations/{recommendation_id}`
  before opening a full evidence drawer.
- Approved recommendation cards can submit feedback through
  `/v1/recommendations/{recommendation_id}/feedback`.
- Feedback forms use stable `signal_id` values so retries are idempotent.
- Instructor-facing soldier detail screens can use
  `/v1/soldiers/{soldier_id}/calibration-profile`.
- Mission views use `/v1/missions/{mission_id}/state` for compact summary and
  `/v1/graph/subgraph` for relationship drilldowns.
- Instructor-facing mission views can use
  `/v1/missions/{mission_id}/team-calibration-profile` for derived team cue
  patterns without changing assessment categories.
- Edited approvals send a full `edited_recommendation` with the same
  `recommendation_id`.
- Blocked recommendations cannot be approved in UI.
- Soldier-facing views use `/v1/soldiers/{soldier_id}/performance`.
- Cross-app drilldowns use entity projection endpoints.
- System 2 drilldowns can use `/v1/soldier/{soldier_id}/training-trajectory`.
- Raw audio/image payloads are not persisted in frontend state after upload.
- Errors `404`, `409`, and `422` have explicit UI states.
- The deployment path provides authentication and network protection before
  exposing the API to users.

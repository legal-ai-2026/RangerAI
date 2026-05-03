from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Phase(str, Enum):
    benning = "Benning"
    mountain = "Mountain"
    florida = "Florida"


class DevelopmentEdge(str, Enum):
    leadership_under_fatigue = "leadership_under_fatigue"
    communications = "communications"
    tactical_patience = "tactical_patience"
    priorities_of_work = "priorities_of_work"
    fire_control = "fire_control"
    decision_speed = "decision_speed"
    team_accountability = "team_accountability"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RunStatus(str, Enum):
    accepted = "accepted"
    processing = "processing"
    pending_approval = "pending_approval"
    completed = "completed"
    failed = "failed"


class GeoPoint(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    grid_mgrs: str = Field(min_length=3, max_length=32)


class IngestEnvelope(StrictModel):
    envelope_id: str = Field(default_factory=lambda: str(uuid4()))
    instructor_id: str = Field(min_length=1)
    platoon_id: str = Field(min_length=1)
    mission_id: str = Field(min_length=1)
    phase: Phase
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    geo: GeoPoint
    audio_b64: str | None = None
    image_b64: list[str] = Field(default_factory=list)
    free_text: str | None = Field(default=None, max_length=20_000)

    @field_validator("timestamp_utc")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp_utc must include timezone information")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def require_some_payload(self) -> "IngestEnvelope":
        if not self.free_text and not self.audio_b64 and not self.image_b64:
            raise ValueError("one of audio_b64, image_b64, or free_text is required")
        return self


class Observation(StrictModel):
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    soldier_id: str = Field(min_length=1)
    task_code: str = Field(default="UNMAPPED", min_length=1)
    note: str = Field(min_length=1, max_length=1200)
    rating: Literal["GO", "NOGO", "UNCERTAIN"] = "UNCERTAIN"
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: Literal["audio", "image", "free_text", "synthetic"] = "synthetic"


class ORBookletRow(StrictModel):
    task_code: str
    task_name: str
    rating: Literal["GO", "NOGO", "UNCERTAIN"]
    observation_note: str | None = None


class ORBookletPage(StrictModel):
    page_id: str = Field(default_factory=lambda: str(uuid4()))
    rows: list[ORBookletRow] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class ScenarioRecommendation(StrictModel):
    recommendation_id: str = Field(default_factory=lambda: str(uuid4()))
    target_soldier_id: str
    rationale: str = Field(min_length=20, max_length=600)
    development_edge: DevelopmentEdge
    proposed_modification: str = Field(min_length=5, max_length=1000)
    doctrine_refs: list[str] = Field(min_length=1)
    safety_checks: list[str] = Field(default_factory=list)
    estimated_duration_min: int = Field(ge=5, le=240)
    requires_resources: list[str] = Field(default_factory=list)
    risk_level: RiskLevel
    fairness_score: float = Field(ge=0, le=1)


class PolicyDecision(StrictModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    fairness_score: float = Field(ge=0, le=1)


class RecommendationRecord(StrictModel):
    recommendation: ScenarioRecommendation
    policy: PolicyDecision
    status: Literal["pending", "approved", "rejected", "blocked"] = "pending"


class RunRecord(StrictModel):
    run_id: str
    status: RunStatus
    ingest: IngestEnvelope
    transcript: str | None = None
    ocr_pages: list[ORBookletPage] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    kg_write_summary: dict[str, int] = Field(default_factory=dict)
    recommendations: list[RecommendationRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ApprovalResponse(StrictModel):
    run_id: str
    recommendation_id: str
    status: Literal["approved", "rejected"]


class RecommendationDecision(StrictModel):
    decision: Literal["approve", "reject"]
    edited_recommendation: ScenarioRecommendation | None = None


class PerformanceMetric(StrictModel):
    name: str
    value: float
    max_value: float
    status: Literal["strong", "watch", "critical"]


class SoldierPerformanceSummary(StrictModel):
    soldier_id: str
    observations_count: int
    go_count: int
    nogo_count: int
    uncertain_count: int
    go_rate: float = Field(ge=0, le=1)
    readiness_score: float = Field(ge=0, le=100)
    metrics: list[PerformanceMetric] = Field(default_factory=list)
    development_edges: list[DevelopmentEdge] = Field(default_factory=list)
    active_recommendations: list[ScenarioRecommendation] = Field(default_factory=list)


class DashboardRunSummary(StrictModel):
    run_id: str
    mission_id: str
    platoon_id: str
    phase: Phase
    status: RunStatus
    total_observations: int
    pending_recommendations: int
    blocked_recommendations: int
    approved_recommendations: int
    platoon_readiness_score: float = Field(ge=0, le=100)
    soldiers: list[SoldierPerformanceSummary] = Field(default_factory=list)


class AuditEvent(StrictModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    event_type: Literal[
        "run_accepted",
        "run_processing_started",
        "run_status_updated",
        "run_failed",
        "run_lease_blocked",
        "recommendation_decision_recorded",
    ]
    actor_id: str | None = None
    recommendation_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OutboxEvent(StrictModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: Literal["recommendation.approved", "recommendation.rejected"]
    aggregate_id: str
    run_id: str
    payload: dict[str, object] = Field(default_factory=dict)
    status: Literal["pending", "published"] = "pending"
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

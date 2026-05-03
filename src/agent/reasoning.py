from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field

from src.agent.interventions import FATIGUE_TERMS, draft_intervention_recommendations
from src.contracts import (
    DevelopmentEdge,
    DoctrineChunk,
    EvidenceRef,
    IngestEnvelope,
    ORBookletPage,
    Observation,
    RecommendationScore,
    RiskLevel,
    ScenarioRecommendation,
    StrictModel,
    TargetIds,
    TerrainSnapshot,
    WeatherSnapshot,
)


class ExtractionUncertainty(StrictModel):
    source_ref: str = Field(min_length=1)
    uncertainty_type: Literal[
        "ambiguous_text",
        "low_model_confidence",
        "ocr_low_confidence",
        "uncertain_rating",
        "unknown_soldier",
    ]
    confidence: float = Field(ge=0, le=1)
    note: str = Field(min_length=1, max_length=700)
    soldier_id: str | None = None
    task_code: str | None = None


class ExtractedObservations(StrictModel):
    observations: list[Observation] = Field(default_factory=list)
    uncertainties: list[ExtractionUncertainty] = Field(default_factory=list)


class CandidateIntervention(StrictModel):
    candidate_id: str = Field(min_length=1)
    intervention_id: str = Field(min_length=1)
    target_soldier_id: str = Field(min_length=1)
    task_code: str | None = None
    development_edge: DevelopmentEdge
    learning_objective: str | None = None
    proposed_modification: str
    doctrine_refs: list[str] = Field(default_factory=list)
    safety_checks: list[str] = Field(default_factory=list)
    estimated_duration_min: int
    requires_resources: list[str] = Field(default_factory=list)
    risk_level: RiskLevel
    score_breakdown: RecommendationScore | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    uncertainty_refs: list[str] = Field(default_factory=list)


class ModelRecommendationDraft(StrictModel):
    rank: int = Field(ge=1, le=3)
    intervention_id: str = Field(min_length=1)
    target_soldier_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_summary: str = Field(min_length=10, max_length=700)
    why_now: str = Field(min_length=10, max_length=700)
    expected_learning_signal: str = Field(min_length=10, max_length=700)
    risk_controls: str = Field(min_length=10, max_length=700)


class ModelRecommendationDrafts(StrictModel):
    recommendations: list[ModelRecommendationDraft] = Field(default_factory=list, max_length=3)


class ReasoningContext(StrictModel):
    run_id: str = Field(min_length=1)
    mission_id: str = Field(min_length=1)
    platoon_id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    mission_type: str = Field(min_length=1)
    observations: list[Observation] = Field(default_factory=list)
    candidate_interventions: list[CandidateIntervention] = Field(default_factory=list)
    soldier_history_refs: dict[str, list[str]] = Field(default_factory=dict)
    recent_recommendation_exposure: dict[str, int] = Field(default_factory=dict)
    kg_observation_refs: dict[str, list[str]] = Field(default_factory=dict)
    doctrine_refs: list[str] = Field(default_factory=list)
    doctrine_chunks: list[DoctrineChunk] = Field(default_factory=list)
    weather: WeatherSnapshot | None = None
    terrain: TerrainSnapshot | None = None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    readiness_signals: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    fatigue_signals: dict[str, float] = Field(default_factory=dict)
    extraction_uncertainties: list[ExtractionUncertainty] = Field(default_factory=list)
    context_confidence: float = Field(default=1.0, ge=0, le=1)
    context_errors: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


def build_reasoning_context(
    *,
    run_id: str,
    ingest: IngestEnvelope,
    observations: list[Observation],
    ocr_pages: list[ORBookletPage],
    kg_observation_refs: dict[str, list[str]],
    extraction_uncertainties: list[ExtractionUncertainty],
    errors: list[str],
    doctrine_chunks: list[DoctrineChunk] | None = None,
    weather: WeatherSnapshot | None = None,
    terrain: TerrainSnapshot | None = None,
) -> ReasoningContext:
    doctrine_chunks = doctrine_chunks or []
    recommendations = draft_intervention_recommendations(
        observations,
        max_recommendations=max(3, min(8, len(observations) or 3)),
    )
    candidates = candidate_interventions_from_recommendations(recommendations)
    doctrine_refs = sorted(
        {ref for recommendation in recommendations for ref in recommendation.doctrine_refs if ref}
        | {chunk.doctrine_ref for chunk in doctrine_chunks}
    )
    ocr_confidence = _ocr_confidence(ocr_pages)
    context_errors = [item for item in errors if "kg" in item.lower() or "doctrine" in item.lower()]
    context_confidence = _context_confidence(
        ocr_confidence=ocr_confidence,
        uncertainties=extraction_uncertainties,
        context_errors=context_errors,
        has_doctrine=bool(doctrine_chunks),
    )
    source_refs = [f"postgres://ranger_runs/{run_id}#record.observations"]
    for refs in kg_observation_refs.values():
        source_refs.extend(refs)
    source_refs.extend(chunk.source_ref for chunk in doctrine_chunks)
    if weather is not None:
        source_refs.append(weather.source_ref)
    if terrain is not None:
        source_refs.append(terrain.source_ref)
    return ReasoningContext(
        run_id=run_id,
        mission_id=ingest.mission_id,
        platoon_id=ingest.platoon_id,
        phase=ingest.phase.value,
        mission_type=ingest.mission_type.value,
        observations=observations,
        candidate_interventions=candidates,
        soldier_history_refs=kg_observation_refs,
        recent_recommendation_exposure={
            soldier_id: len(refs) for soldier_id, refs in kg_observation_refs.items()
        },
        kg_observation_refs=kg_observation_refs,
        doctrine_refs=doctrine_refs,
        doctrine_chunks=doctrine_chunks,
        weather=weather,
        terrain=terrain,
        ocr_confidence=ocr_confidence,
        readiness_signals=_readiness_signals(observations),
        fatigue_signals=_fatigue_signals(observations),
        extraction_uncertainties=extraction_uncertainties,
        context_confidence=context_confidence,
        context_errors=context_errors,
        source_refs=sorted(set(source_refs)),
    )


def candidate_interventions_from_recommendations(
    recommendations: list[ScenarioRecommendation],
) -> list[CandidateIntervention]:
    return [
        CandidateIntervention(
            candidate_id=f"{item.intervention_id or 'intervention'}:{item.target_soldier_id}",
            intervention_id=item.intervention_id or "unknown_intervention",
            target_soldier_id=item.target_soldier_id,
            task_code=item.target_ids.task_code,
            development_edge=item.development_edge,
            learning_objective=item.learning_objective,
            proposed_modification=item.proposed_modification,
            doctrine_refs=list(item.doctrine_refs),
            safety_checks=list(item.safety_checks),
            estimated_duration_min=item.estimated_duration_min,
            requires_resources=list(item.requires_resources),
            risk_level=item.risk_level,
            score_breakdown=item.score_breakdown,
            evidence_refs=list(item.evidence_refs),
            uncertainty_refs=list(item.uncertainty_refs),
        )
        for item in recommendations
    ]


def apply_model_drafts_to_recommendations(
    recommendations: list[ScenarioRecommendation],
    drafts: list[ModelRecommendationDraft],
    *,
    model_name: str,
) -> list[ScenarioRecommendation]:
    by_key = {
        (item.intervention_id, item.target_soldier_id): item
        for item in recommendations
        if item.intervention_id
    }
    by_intervention: dict[str, ScenarioRecommendation] = {}
    for item in recommendations:
        if item.intervention_id and item.intervention_id not in by_intervention:
            by_intervention[item.intervention_id] = item

    selected: list[ScenarioRecommendation] = []
    seen: set[tuple[str | None, str]] = set()
    for draft in sorted(drafts, key=lambda item: item.rank):
        base = by_key.get((draft.intervention_id, draft.target_soldier_id))
        if base is None:
            base = by_intervention.get(draft.intervention_id)
        if base is None:
            continue
        key = (draft.intervention_id, draft.target_soldier_id)
        if key in seen:
            continue
        seen.add(key)
        selected.append(_apply_model_draft(base, draft, model_name=model_name))
        if len(selected) >= 3:
            break
    return selected


def extraction_uncertainties_for_observations(
    observations: list[Observation],
    *,
    source_ref: str,
) -> list[ExtractionUncertainty]:
    uncertainties: list[ExtractionUncertainty] = []
    for observation in observations:
        confidence = observation.confidence if observation.confidence is not None else 0.8
        if observation.soldier_id == "UNKNOWN":
            uncertainties.append(
                ExtractionUncertainty(
                    source_ref=source_ref,
                    uncertainty_type="unknown_soldier",
                    confidence=confidence,
                    note=f"Extractor could not validate a soldier for {observation.task_code}.",
                    soldier_id=observation.soldier_id,
                    task_code=observation.task_code,
                )
            )
        if observation.rating == "UNCERTAIN":
            uncertainties.append(
                ExtractionUncertainty(
                    source_ref=source_ref,
                    uncertainty_type="uncertain_rating",
                    confidence=confidence,
                    note=f"Rating is uncertain for {observation.soldier_id} on {observation.task_code}.",
                    soldier_id=observation.soldier_id,
                    task_code=observation.task_code,
                )
            )
        if confidence < 0.55:
            uncertainties.append(
                ExtractionUncertainty(
                    source_ref=source_ref,
                    uncertainty_type="low_model_confidence",
                    confidence=confidence,
                    note=f"Extraction confidence {confidence:.2f} is below the recommendation threshold.",
                    soldier_id=observation.soldier_id,
                    task_code=observation.task_code,
                )
            )
    return uncertainties


def _apply_model_draft(
    base: ScenarioRecommendation,
    draft: ModelRecommendationDraft,
    *,
    model_name: str,
) -> ScenarioRecommendation:
    rationale = _rationale_from_draft(draft)
    target_ids = TargetIds(
        soldier_id=draft.target_soldier_id,
        platoon_id=base.target_ids.platoon_id,
        patrol_id=base.target_ids.patrol_id,
        mission_id=base.target_ids.mission_id,
        task_code=base.target_ids.task_code,
    )
    return base.model_copy(
        update={
            "target_soldier_id": draft.target_soldier_id,
            "rationale": rationale,
            "target_ids": target_ids,
            "evidence_summary": draft.evidence_summary,
            "why_now": draft.why_now,
            "expected_learning_signal": draft.expected_learning_signal,
            "risk_controls": draft.risk_controls,
            "model_context_refs": [
                *base.model_context_refs,
                f"model://openai/{model_name}#recommendation_rank_{draft.rank}",
            ],
            "policy_refs": [
                *base.policy_refs,
                "policy:curated-intervention-library-only",
                "policy:instructor-approval-required",
            ],
        }
    )


def _rationale_from_draft(draft: ModelRecommendationDraft) -> str:
    text = (
        f"{draft.evidence_summary} {draft.why_now} Expected signal: "
        f"{draft.expected_learning_signal}"
    )
    return text[:600]


def _ocr_confidence(ocr_pages: list[ORBookletPage]) -> float | None:
    if not ocr_pages:
        return None
    return round(sum(page.confidence for page in ocr_pages) / len(ocr_pages), 2)


def _context_confidence(
    *,
    ocr_confidence: float | None,
    uncertainties: list[ExtractionUncertainty],
    context_errors: list[str],
    has_doctrine: bool,
) -> float:
    confidence = 1.0
    if ocr_confidence is not None and ocr_confidence < 0.7:
        confidence -= 0.15
    confidence -= min(0.35, len(uncertainties) * 0.06)
    if context_errors:
        confidence -= 0.15
    if not has_doctrine:
        confidence -= 0.1
    return round(max(0.0, min(1.0, confidence)), 2)


def _readiness_signals(observations: list[Observation]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[Observation]] = {}
    for observation in observations:
        grouped.setdefault(observation.soldier_id, []).append(observation)

    signals: dict[str, dict[str, float | int]] = {}
    for soldier_id, items in grouped.items():
        counts = Counter(item.rating for item in items)
        total = len(items)
        go_rate = round(counts["GO"] / total, 2) if total else 0.0
        readiness = max(0, min(100, 70 + (counts["GO"] * 10) - (counts["NOGO"] * 15)))
        signals[soldier_id] = {
            "observations": total,
            "go": counts["GO"],
            "nogo": counts["NOGO"],
            "uncertain": counts["UNCERTAIN"],
            "go_rate": go_rate,
            "readiness_score": float(readiness),
        }
    return signals


def _fatigue_signals(observations: list[Observation]) -> dict[str, float]:
    signals: dict[str, float] = {}
    for observation in observations:
        score = 0.3 if any(term in observation.note.lower() for term in FATIGUE_TERMS) else 0.0
        signals[observation.soldier_id] = max(signals.get(observation.soldier_id, 0.0), score)
    return signals

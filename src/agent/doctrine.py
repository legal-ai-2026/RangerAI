from __future__ import annotations

from src.contracts import DoctrineChunk, Observation


SEED_DOCTRINE: tuple[DoctrineChunk, ...] = (
    DoctrineChunk(
        doctrine_ref="TC 3-21.76 MV-2",
        task_code="MV-2",
        title="Movement reporting and control",
        text=(
            "Movement observations should evaluate whether leaders maintain control, "
            "report phase-line or checkpoint progress, and pass concise SITREPs or "
            "FRAGOs without losing security or tempo."
        ),
        source_ref="asset://doctrine/tc-3-21-76#MV-2",
        confidence=0.82,
    ),
    DoctrineChunk(
        doctrine_ref="TC 3-21.76 PB-7",
        task_code="PB-7",
        title="Patrol-base security and priorities of work",
        text=(
            "Patrol-base observations should evaluate security, priorities of work, "
            "leadership checks, rest plans, and controls that keep the element alert "
            "without adding unsafe or punitive physical load."
        ),
        source_ref="asset://doctrine/tc-3-21-76#PB-7",
        confidence=0.82,
    ),
    DoctrineChunk(
        doctrine_ref="TC 3-21.76 AM-4",
        task_code="AM-4",
        title="Ambush initiation and fire control",
        text=(
            "Ambush observations should evaluate whether leaders rehearse initiation, "
            "fire-control cues, lift and shift signals, and element responsibilities "
            "before execution."
        ),
        source_ref="asset://doctrine/tc-3-21-76#AM-4",
        confidence=0.82,
    ),
    DoctrineChunk(
        doctrine_ref="TC 3-21.76 leadership",
        task_code="leadership",
        title="Leadership under ambiguity",
        text=(
            "Leadership observations should evaluate clear intent, delegation, "
            "timeline control, and subordinate confirmation when the situation is "
            "ambiguous or fatigue is present."
        ),
        source_ref="asset://doctrine/tc-3-21-76#leadership",
        confidence=0.72,
    ),
)


def lookup_doctrine_chunks(
    observations: list[Observation],
    doctrine_refs: list[str],
) -> list[DoctrineChunk]:
    task_codes = {
        observation.task_code
        for observation in observations
        if observation.task_code and observation.task_code != "UNMAPPED"
    }
    normalized_refs = {ref.lower() for ref in doctrine_refs}
    chunks: list[DoctrineChunk] = []
    for chunk in SEED_DOCTRINE:
        if chunk.task_code in task_codes or chunk.doctrine_ref.lower() in normalized_refs:
            chunks.append(chunk)
            continue
        if chunk.task_code == "leadership" and any(
            term in observation.note.lower()
            for observation in observations
            for term in ("fatigue", "decision", "leader", "ambiguous")
        ):
            chunks.append(chunk)
    return _dedupe_chunks(chunks)


def _dedupe_chunks(chunks: list[DoctrineChunk]) -> list[DoctrineChunk]:
    deduped: dict[str, DoctrineChunk] = {}
    for chunk in chunks:
        deduped.setdefault(chunk.source_ref, chunk)
    return sorted(deduped.values(), key=lambda item: item.source_ref)

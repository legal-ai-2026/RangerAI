from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.contracts import GeoPoint, IngestEnvelope, Phase, RecommendationDecision


def test_ingest_rejects_missing_payload() -> None:
    with pytest.raises(ValidationError):
        IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            timestamp_utc=datetime.now(timezone.utc),
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
        )


def test_ingest_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            timestamp_utc=datetime.now(timezone.utc),
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            free_text="Jones blew Phase Line Bird",
            unexpected=True,
        )


def test_recommendation_decision_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        RecommendationDecision(decision="defer")

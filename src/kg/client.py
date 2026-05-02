from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.config import settings
from src.contracts import IngestEnvelope, Observation, ScenarioRecommendation


@dataclass
class KGClient:
    host: str = settings.falkordb_host
    port: int = settings.falkordb_port
    graph_name: str = settings.falkordb_graph
    _graph: Any = field(default=None, init=False, repr=False)

    def connect(self) -> Any:
        if self._graph is None:
            from falkordb import FalkorDB

            self._graph = FalkorDB(host=self.host, port=self.port).select_graph(self.graph_name)
        return self._graph

    def health(self) -> bool:
        try:
            self.connect().query("RETURN 1")
            return True
        except Exception:
            return False

    def ensure_schema(self) -> None:
        graph = self.connect()
        try:
            graph.query("CREATE INDEX ON :Observation(observation_id)")
            graph.query("CREATE INDEX ON :Recommendation(recommendation_id)")
            graph.query(
                "CALL db.idx.vector.createNodeIndex('Observation','embedding',1536,'COSINE')"
            )
        except Exception:
            # FalkorDB returns errors when indexes already exist or vector support differs.
            pass

    def write_observations(
        self, ingest: IngestEnvelope, observations: list[Observation]
    ) -> dict[str, int]:
        graph = self.connect()
        self.ensure_schema()
        for obs in observations:
            graph.query(
                """
                MERGE (m:Mission {mission_id:$mission_id})
                MERGE (p:Platoon {platoon_id:$platoon_id})
                MERGE (s:Soldier {soldier_id:$soldier_id})
                MERGE (t:Task {task_code:$task_code})
                MERGE (o:Observation {observation_id:$observation_id})
                SET o.note=$note, o.rating=$rating, o.timestamp=$timestamp
                MERGE (s)-[:MEMBER_OF]->(p)
                MERGE (p)-[:PART_OF]->(m)
                MERGE (s)-[:HAS_OBSERVATION {timestamp:$timestamp}]->(o)
                MERGE (o)-[:ON_TASK]->(t)
                """,
                {
                    "mission_id": ingest.mission_id,
                    "platoon_id": ingest.platoon_id,
                    "soldier_id": obs.soldier_id,
                    "task_code": obs.task_code,
                    "observation_id": obs.observation_id,
                    "note": obs.note,
                    "rating": obs.rating,
                    "timestamp": obs.timestamp_utc.isoformat(),
                },
            )
        return {"observations": len(observations)}

    def write_recommendation(self, recommendation: ScenarioRecommendation) -> None:
        graph = self.connect()
        graph.query(
            """
            MERGE (r:Recommendation {recommendation_id:$recommendation_id})
            SET r.target_soldier_id=$target_soldier_id,
                r.rationale=$rationale,
                r.risk_level=$risk_level,
                r.fairness_score=$fairness_score
            MERGE (s:Soldier {soldier_id:$target_soldier_id})
            MERGE (r)-[:TARGETS]->(s)
            """,
            recommendation.model_dump(mode="json"),
        )

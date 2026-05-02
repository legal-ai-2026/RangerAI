from __future__ import annotations

from dataclasses import dataclass, field

from src.contracts import RunRecord


@dataclass
class InMemoryRunStore:
    records: dict[str, RunRecord] = field(default_factory=dict)

    def put(self, record: RunRecord) -> None:
        self.records[record.run_id] = record

    def get(self, run_id: str) -> RunRecord | None:
        return self.records.get(run_id)

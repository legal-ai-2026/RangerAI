# Implementation Notes

## Build Priorities

1. Keep `POST /v1/ingest -> GET /v1/runs/{id} -> dashboard summary -> approve/reject` working at all times.
2. Prefer typed Pydantic contracts over ad hoc dictionaries.
3. Keep external model and database access behind adapters so tests remain deterministic.
4. Do not add a frontend to this repository.
5. Treat `/v1` routes as canonical; keep unversioned aliases only for early compatibility.

## Demo Path

Run the API locally, then execute:

```bash
make demo
```

The demo posts `assets/fixtures/envelopes/mountain_phase_amb_01.json` to `http://localhost:8001/v1/ingest`. Poll `GET /v1/runs/{run_id}` until recommendations are pending, then approve or reject a recommendation through `POST /v1/recommendations/{id}/decision`.

## Cut List

If time is constrained, cut in this order:

1. Live OCR fallback quality improvements.
2. Weather and terrain enrichment.
3. Prompt regression breadth.
4. Langfuse self-hosting.
5. FalkorDB browser helper polish.

Do not cut policy filtering, fairness scoring, Pydantic validation, or instructor approval.

## Failure-Mode Script

Use fixtures and tests to preserve these defenses:

- Unsafe cold-water recommendation: policy must block high-risk immersion-style changes.
- Doctrine contradiction: policy/prompt regression should catch missing or conflicting TC 3-21.76 references.
- Repeated targeting: fairness spread must reject over-targeting a single soldier.
- Hallucinated soldier: recommendations must validate against the observed roster.
- Smudged OR page: uncertain OCR should remain `UNCERTAIN` and reviewable.
- OPSEC leak: precise MGRS or protected personal data must be redacted before model calls and output.
- Prompt injection: instructor text must not override guardrails.

## Code Patterns

- Add new provider integrations as methods on `ProviderClients` or behind a similarly narrow adapter.
- Keep deterministic fallback behavior for local tests.
- Add tests before changing guardrails.
- Treat `assets/ground-truth/` as authoritative if present.
- Put model names in `src.agent.models`; do not inline provider model strings.
- Dashboard-facing DTOs should stay backend-only and presentation-neutral: status names, metric values, and recommendations, but no React/Tailwind/browser code.

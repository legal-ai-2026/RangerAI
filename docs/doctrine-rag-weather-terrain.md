# Doctrine RAG, Weather, Terrain, and Planner Posture

## Current Status

System 1 has a bounded first-pass enrichment implementation for doctrine,
weather, and terrain context. It defaults to deterministic local context for
tests and demos, with live weather and terrain hooks available when explicitly
configured.

Implemented now:

- `ScenarioRecommendation.doctrine_refs` and doctrine `EvidenceRef` locators.
- `PgVectorStore` for semantic document storage and search.
- `ReasoningContext.doctrine_refs`, source refs, context confidence, and
  candidate intervention metadata.
- `ReasoningContext.doctrine_chunks`, `weather`, and `terrain`.
- Seed doctrine lookup for currently supported task codes.
- Synthetic weather and terrain fixtures for deterministic local operation.
- Live NWS/Open-Meteo weather and USGS EPQS terrain hooks.
- FalkorDB writes for observations and approved recommendation provenance.

Still pending:

- Doctrine chunk ingestion into pgvector.
- Embedding generation for doctrine chunks and observations.
- Rich terrain derivation from DEM/hydrography beyond point elevation and
  synthetic slope/water classes.
- Prompt regression tests that prove doctrine retrieval changes neither policy
  bypass nor instructor approval.

## Doctrine RAG Design

Doctrine RAG should stay retrieval-first and bounded. The model may summarize or
rank retrieved context, but it must not invent doctrine references, task codes,
or scenario modifications.

Target data flow:

```text
assets/doctrine/*.md or curated excerpts
  -> chunk by task / heading / paragraph
  -> embed with pinned embedding model
  -> upsert into pgvector namespace=doctrine
  -> enrich node retrieves by task_code, observation note, and intervention_id
  -> reason node ranks only curated candidate interventions
  -> policy verifies TC 3-21.76 refs and safety constraints
  -> human gate requires instructor approval before emit
```

Minimum doctrine chunk metadata:

- `document_id`: stable slug, for example `tc-3-21-76-mv-2-sitrep`.
- `namespace`: `doctrine`.
- `text`: short doctrine excerpt or paraphrased unclassified training standard.
- `metadata.task_code`: task code such as `MV-2`, `PB-7`, or `AM-4`.
- `metadata.doctrine_ref`: human-readable ref such as `TC 3-21.76 MV-2`.
- `metadata.source`: local fixture, approved excerpt, or authoritative source.
- `metadata.releasability`: `public`, `training_approved`, or `restricted`.

Acceptance criteria:

- Recommendations with missing or unsupported doctrine remain blocked.
- The reason node can only use retrieved doctrine chunks or curated
  intervention templates.
- The pending approval payload includes doctrine evidence refs.
- Tests cover doctrine contradiction, missing doctrine, and no free-form
  doctrine invention.

## Weather API Recommendation

Use the National Weather Service API as the primary live weather source for
United States training areas.

Reasons:

- Official U.S. government source.
- Free open data.
- No API key today; NWS requires a unique `User-Agent` header.
- Supports point-to-grid forecast discovery, hourly forecasts, observations,
  alerts, and cache-friendly response lifecycles.

Use Open-Meteo as an optional fallback for development, non-commercial use, or
non-U.S. synthetic/demo runs.

Reasons:

- Free for non-commercial use.
- No API key required.
- Global coverage with simple JSON forecast endpoints.
- Easier to use in smoke tests when NWS point/grid semantics are unnecessary.

Implementation default:

1. Try synthetic weather when `WEATHER_PROVIDER=synthetic` or provider keys are
   absent in local tests.
2. Try NWS when coordinates are inside U.S. coverage and a service User-Agent is
   configured.
3. Try Open-Meteo only as a non-commercial/demo fallback.
4. Mark weather context degraded when every live provider fails; do not block
   processing unless policy needs weather to evaluate a safety-critical action.

Minimum weather fields for policy:

- temperature, apparent temperature, wind speed/gust, precipitation probability,
  precipitation amount, forecast timestamp, active alert labels, provider,
  source URL, and freshness.

## Terrain API Recommendation

Use USGS National Map / 3DEP services as the primary U.S. terrain source, with
synthetic fixtures for deterministic tests.

Recommended source split:

- Point elevation: USGS Elevation Point Query Service (EPQS), backed by 3DEP.
- Broader terrain products: The National Map downloadable/web services for DEM,
  hydrography, contours, boundaries, and related layers.
- Global or non-U.S. fallback: OpenTopography or self-hosted Open Topo Data when
  a free API key or local DEM dataset is acceptable.

Important limitation:

EPQS returns point elevation. It does not by itself provide slope class, water
features, vegetation, trafficability, or terrain classification. For policy
checks that need slope or water risk, System 1 should either derive those fields
from local DEM/hydrography fixtures or consume a precomputed terrain fixture.

Implementation default:

1. Use synthetic terrain fixtures in tests and demos.
2. Use USGS EPQS for U.S. point elevation when live terrain is enabled.
3. Use cached local terrain fixtures for slope, water proximity, terrain class,
   and known training-lane hazards.
4. Treat live terrain as advisory unless the policy gate explicitly requires a
   terrain safety check.

Minimum terrain fields for policy:

- elevation, slope class, water proximity, terrain class, known hazard labels,
  source URL, provider, generated timestamp, and confidence.

## Planner Posture

Do not migrate System 1 into an open-ended autonomous multi-agent planner.

That would conflict with the repository operating boundary:

- System 1 is not a chatbot.
- It runs once per `IngestEnvelope`.
- It recommends; the instructor decides.
- It does not autonomously change schedules, emit actions, or call Systems 2/3
  during the agent loop.
- LangGraph is used for a bounded workflow with replay and a human interrupt,
  not as an open-ended multi-agent crew.

Acceptable evolution:

- Keep the single bounded workflow.
- Add deterministic retrieval tools for doctrine, weather, terrain, and history.
- Add a planner-like node only if it produces typed `CandidateIntervention`
  objects from approved tools and curated templates.
- Preserve policy filtering and instructor approval as mandatory gates.

Rejected evolution:

- Autonomous task decomposition that can invent tools or objectives.
- Multi-agent debate that can bypass policy.
- Direct operational action without instructor approval.
- Free-form chat or long-running autonomous mission control.

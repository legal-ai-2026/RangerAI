from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Literal, cast

from tenacity import retry, stop_after_attempt, wait_exponential

from src.agent.evaluation import (
    ProviderDiagnostic,
    ProviderDiagnosticStatus,
    ProviderStage,
)
from src.agent.interventions import draft_intervention_recommendations
from src.agent.models import (
    MODEL_DEEPGRAM_NOVA3,
    MODEL_GPT_4O_TRANSCRIBE,
    MODEL_MISTRAL_OCR,
)
from src.agent.reasoning import (
    ExtractedObservations,
    ExtractionUncertainty,
    ModelRecommendationDraft,
    ModelRecommendationDrafts,
    ReasoningContext,
    apply_model_drafts_to_recommendations,
    extraction_uncertainties_for_observations,
)
from src.config import Settings, settings
from src.contracts import ORBookletPage, ORBookletRow, Observation, ScenarioRecommendation
from src.ingest.scrub import scrub_sensitive_text


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderClients:
    settings: Settings = settings
    diagnostics: list[ProviderDiagnostic] = field(default_factory=list)

    async def transcribe(self, audio_b64: str) -> str:
        try:
            transcript = self._openai_transcribe(audio_b64, model=self.settings.openai_stt_model)
            self._record_provider_diagnostic(
                stage="stt",
                provider="openai",
                status="applied",
                model=self.settings.openai_stt_model,
            )
            return transcript
        except Exception as primary_error:
            if self.settings.deepgram_api_key:
                try:
                    transcript = self._deepgram_transcribe(audio_b64)
                    self._record_provider_diagnostic(
                        stage="stt",
                        provider="deepgram",
                        status="fallback",
                        model=MODEL_DEEPGRAM_NOVA3,
                        message=_diagnostic_message(primary_error),
                    )
                    return transcript
                except Exception as fallback_error:
                    self._record_provider_diagnostic(
                        stage="stt",
                        provider="deepgram",
                        status="failed",
                        model=MODEL_DEEPGRAM_NOVA3,
                        message=_diagnostic_message(fallback_error),
                    )
                    pass
            self._record_provider_diagnostic(
                stage="stt",
                provider="openai",
                status="failed",
                model=self.settings.openai_stt_model,
                message=_diagnostic_message(primary_error),
            )
            raise ProviderError(
                f"STT failed and OpenAI fallback is not configured: {primary_error}"
            )

    async def ocr_pages(self, image_b64: list[str]) -> list[ORBookletPage]:
        pages: list[ORBookletPage] = []
        for image in image_b64:
            try:
                pages.append(self._openai_vision_ocr(image))
                self._record_provider_diagnostic(
                    stage="ocr",
                    provider="openai",
                    status="applied",
                    model=self.settings.openai_multimodal_model,
                )
            except Exception as primary_error:
                try:
                    pages.append(self._mistral_ocr(image))
                    self._record_provider_diagnostic(
                        stage="ocr",
                        provider="mistral",
                        status="fallback",
                        model=MODEL_MISTRAL_OCR,
                        message=_diagnostic_message(primary_error),
                    )
                except Exception as fallback_error:
                    try:
                        pages.append(self._claude_vision_ocr(image))
                        self._record_provider_diagnostic(
                            stage="ocr",
                            provider="anthropic",
                            status="fallback",
                            model=self.settings.anthropic_model,
                            message=_diagnostic_message(fallback_error),
                        )
                    except Exception as final_error:
                        self._record_provider_diagnostic(
                            stage="ocr",
                            provider="anthropic",
                            status="failed",
                            model=self.settings.anthropic_model,
                            message=_diagnostic_message(final_error),
                        )
                        raise
        return pages

    async def extract_observations(self, text: str) -> list[Observation]:
        return (await self.extract_observations_with_uncertainty(text)).observations

    async def extract_observations_with_uncertainty(self, text: str) -> ExtractedObservations:
        scrubbed = scrub_sensitive_text(text)
        if self.settings.openai_api_key:
            try:
                extracted = self._openai_extract_observations(scrubbed)
                self._record_provider_diagnostic(
                    stage="extraction",
                    provider="openai",
                    status="applied",
                    model=self.settings.openai_extraction_model,
                )
                return extracted
            except Exception as primary_error:
                openai_error = primary_error
                pass
        else:
            openai_error = ProviderError("OPENAI_API_KEY is not configured")
        if self.settings.anthropic_api_key:
            try:
                claude_items = self._claude_json(
                    "Extract Ranger School observations as JSON array with soldier_id, "
                    "task_code, note, rating, and source. Use rating UNCERTAIN when unclear.",
                    scrubbed,
                )
                observations = [Observation.model_validate(item) for item in claude_items]
                self._record_provider_diagnostic(
                    stage="extraction",
                    provider="anthropic",
                    status="fallback",
                    model=self.settings.anthropic_model,
                    message=_diagnostic_message(openai_error),
                )
                return ExtractedObservations(
                    observations=observations,
                    uncertainties=extraction_uncertainties_for_observations(
                        observations,
                        source_ref="model://anthropic/extract_observations",
                    ),
                )
            except Exception as fallback_error:
                self._record_provider_diagnostic(
                    stage="extraction",
                    provider="anthropic",
                    status="failed",
                    model=self.settings.anthropic_model,
                    message=_diagnostic_message(fallback_error),
                )
                pass
        observations = heuristic_observations(scrubbed)
        self._record_provider_diagnostic(
            stage="extraction",
            provider="heuristic",
            status="fallback",
            message=_diagnostic_message(openai_error),
        )
        return ExtractedObservations(
            observations=observations,
            uncertainties=extraction_uncertainties_for_observations(
                observations,
                source_ref="heuristic://extract_observations",
            ),
        )

    async def draft_recommendations(
        self,
        observations: list[Observation],
        reasoning_context: ReasoningContext | None = None,
    ) -> list[ScenarioRecommendation]:
        if not any(
            item.soldier_id not in {"", "UNKNOWN"} and item.rating != "UNCERTAIN"
            for item in observations
        ):
            return []
        max_recommendations = max(3, min(8, len(observations) or 3))
        library_recommendations = draft_intervention_recommendations(
            observations,
            max_recommendations=max_recommendations,
        )
        if (
            reasoning_context is not None
            and library_recommendations
            and self.settings.openai_api_key
        ):
            try:
                model_drafts = self._openai_recommendation_drafts(reasoning_context)
                reasoned = apply_model_drafts_to_recommendations(
                    library_recommendations,
                    model_drafts,
                    model_name=self.settings.openai_reasoning_model,
                )
                if reasoned:
                    self._record_provider_diagnostic(
                        stage="recommendation_ranking",
                        provider="openai",
                        status="applied",
                        model=self.settings.openai_reasoning_model,
                    )
                    return reasoned
                self._record_provider_diagnostic(
                    stage="recommendation_ranking",
                    provider="library",
                    status="fallback",
                    message="OpenAI returned no matching curated recommendation drafts.",
                )
            except Exception as exc:
                self._record_provider_diagnostic(
                    stage="recommendation_ranking",
                    provider="library",
                    status="fallback",
                    message=_diagnostic_message(exc),
                )
                pass
        else:
            self._record_provider_diagnostic(
                stage="recommendation_ranking",
                provider="library",
                status="applied",
                message="Recommendation ranking used the curated intervention library.",
            )
        return library_recommendations[:3]

    def _record_provider_diagnostic(
        self,
        *,
        stage: ProviderStage,
        provider: str,
        status: ProviderDiagnosticStatus,
        model: str | None = None,
        message: str | None = None,
    ) -> None:
        self.diagnostics.append(
            ProviderDiagnostic(
                stage=stage,
                provider=provider,
                status=status,
                model=model,
                message=message[:700] if message else None,
            )
        )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.2, max=2))
    def _deepgram_transcribe(self, audio_b64: str) -> str:
        if not self.settings.deepgram_api_key:
            raise ProviderError("DEEPGRAM_API_KEY is not configured")
        audio = base64.b64decode(audio_b64)
        req = urllib.request.Request(
            f"https://api.deepgram.com/v1/listen?model={MODEL_DEEPGRAM_NOVA3}&smart_format=true"
            "&keywords=LRRP&keywords=ORP&keywords=SALUTE&keywords=CASEVAC"
            "&keywords=FRAGO&keywords=SITREP&keywords=patrol%20base",
            data=audio,
            headers={
                "Authorization": f"Token {self.settings.deepgram_api_key}",
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read())
        return data["results"]["channels"][0]["alternatives"][0]["transcript"]

    def _openai_transcribe(self, audio_b64: str, model: str = MODEL_GPT_4O_TRANSCRIBE) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("openai package is not installed") from exc
        if not self.settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        audio = BytesIO(base64.b64decode(audio_b64))
        audio.name = "audio.wav"
        client = OpenAI(api_key=self.settings.openai_api_key)
        result = client.audio.transcriptions.create(model=model, file=audio)
        return str(result.text)

    def _openai_vision_ocr(self, image_b64: str) -> ORBookletPage:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("openai package is not installed") from exc
        if not self.settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        client = OpenAI(api_key=self.settings.openai_api_key)
        response = client.chat.completions.create(
            model=self.settings.openai_multimodal_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Read this Ranger School OR booklet photo. Return JSON with "
                                "confidence and rows. rows must contain task_code, task_name, "
                                "rating as GO/NOGO/UNCERTAIN, and observation_note. Mark smudged "
                                "or unclear entries UNCERTAIN."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        rows = [
            ORBookletRow(
                task_code=str(item.get("task_code", "OR-UNCERTAIN")),
                task_name=str(item.get("task_name", "OR booklet row")),
                rating=item.get("rating", "UNCERTAIN")
                if item.get("rating") in {"GO", "NOGO", "UNCERTAIN"}
                else "UNCERTAIN",
                observation_note=item.get("observation_note"),
            )
            for item in parsed.get("rows", [])
        ]
        if not rows:
            rows = [
                ORBookletRow(
                    task_code="OR-UNCERTAIN",
                    task_name="OR booklet row",
                    rating="UNCERTAIN",
                    observation_note=str(parsed)[:500],
                )
            ]
        confidence = float(parsed.get("confidence", 0.7))
        if confidence > 1:
            confidence = confidence / 100
        return ORBookletPage(rows=rows, confidence=max(0.0, min(1.0, confidence)))

    def _openai_extract_observations(self, text: str) -> ExtractedObservations:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("openai package is not installed") from exc
        if not self.settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        client = OpenAI(api_key=self.settings.openai_api_key)
        parsed = _openai_json_object(
            client=client,
            model=self.settings.openai_extraction_model,
            system_content=(
                "You extract Ranger School performance observations. Return only JSON "
                "with observations and uncertainties. Do not invent soldiers or task "
                "codes; use soldier_id UNKNOWN and rating UNCERTAIN when ambiguous."
            ),
            user_content=(
                "Return JSON shaped as "
                '{"observations":[{"soldier_id":"Jones","task_code":"MV-2",'
                '"note":"brief note","rating":"NOGO","source":"free_text",'
                '"confidence":0.82}],"uncertainties":[{"source_ref":"input://text",'
                '"uncertainty_type":"ambiguous_text","confidence":0.4,'
                '"note":"why uncertain","soldier_id":"UNKNOWN","task_code":"UNMAPPED"}]}. '
                "Allowed rating values are GO, NOGO, UNCERTAIN. Allowed source values "
                "are audio, image, free_text, synthetic.\n\nInput:\n"
                f"{text}"
            ),
        )
        observations = [
            _observation_from_model_item(item)
            for item in parsed.get("observations", [])
            if isinstance(item, dict)
        ]
        uncertainties = [
            _uncertainty_from_model_item(item, self.settings.openai_extraction_model)
            for item in parsed.get("uncertainties", [])
            if isinstance(item, dict)
        ]
        uncertainties.extend(
            extraction_uncertainties_for_observations(
                observations,
                source_ref=f"model://openai/{self.settings.openai_extraction_model}"
                "#extract_observations",
            )
        )
        return ExtractedObservations(observations=observations, uncertainties=uncertainties)

    def _openai_recommendation_drafts(
        self,
        reasoning_context: ReasoningContext,
    ) -> list[ModelRecommendationDraft]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("openai package is not installed") from exc
        if not self.settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        client = OpenAI(api_key=self.settings.openai_api_key)
        payload = reasoning_context.model_dump(mode="json")
        parsed = _openai_json_object(
            client=client,
            model=self.settings.openai_reasoning_model,
            system_content=(
                "You are a retrieval-first AI instructor copilot. Select and rank up "
                "to three candidate interventions from candidate_interventions only. "
                "Do not invent modifications, doctrine, soldiers, or task standards. "
                "Explain evidence, why now, learning signal, and risk controls."
            ),
            user_content=(
                "Return JSON shaped as "
                '{"recommendations":[{"rank":1,"intervention_id":"comm_degraded_sitrep",'
                '"target_soldier_id":"Jones","confidence":0.82,'
                '"evidence_summary":"...","why_now":"...",'
                '"expected_learning_signal":"...","risk_controls":"..."}]}.\n\n'
                f"ReasoningContext:\n{json.dumps(payload, sort_keys=True)}"
            ),
        )
        drafts = ModelRecommendationDrafts.model_validate(parsed)
        return drafts.recommendations

    def _mistral_ocr(self, image_b64: str) -> ORBookletPage:
        try:
            from mistralai import Mistral
        except ImportError as exc:
            raise ProviderError("mistralai package is not installed") from exc
        if not self.settings.mistral_api_key:
            raise ProviderError("MISTRAL_API_KEY is not configured")
        client = cast(Any, Mistral(api_key=self.settings.mistral_api_key))
        document = {
            "type": "image_url",
            "image_url": f"data:image/jpeg;base64,{image_b64}",
        }
        response = client.ocr.process(model=MODEL_MISTRAL_OCR, document=document)
        markdown = "\n".join(page.markdown for page in response.pages)
        rows = [
            ORBookletRow(
                task_code="OR-UNCERTAIN",
                task_name="OR booklet row",
                rating="UNCERTAIN",
                observation_note=markdown[:500],
            )
        ]
        return ORBookletPage(rows=rows, confidence=0.75)

    def _claude_vision_ocr(self, image_b64: str) -> ORBookletPage:
        if not self.settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not configured for OCR fallback")
        data = self._anthropic_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Read this OR booklet page. Return brief text only.",
                        },
                    ],
                }
            ],
            max_tokens=700,
        )
        text = _content_text(data)
        return ORBookletPage(
            rows=[
                ORBookletRow(
                    task_code="OR-UNCERTAIN",
                    task_name="OR booklet row",
                    rating="UNCERTAIN",
                    observation_note=text[:500],
                )
            ],
            confidence=0.55,
        )

    def _claude_json(self, instruction: str, content: str) -> list[dict[str, Any]]:
        data = self._anthropic_messages(
            [
                {
                    "role": "user",
                    "content": (
                        f"{instruction}\nReturn only valid JSON, no markdown.\n\nInput:\n{content}"
                    ),
                }
            ],
            max_tokens=1600,
        )
        text = _content_text(data).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ProviderError("Claude did not return a JSON array")
        return parsed

    def _anthropic_messages(
        self, messages: list[dict[str, Any]], max_tokens: int
    ) -> dict[str, Any]:
        if not self.settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not configured")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(
                {
                    "model": self.settings.anthropic_model,
                    "max_tokens": max_tokens,
                    "messages": messages,
                }
            ).encode(),
            headers={
                "x-api-key": self.settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ProviderError(exc.read().decode()) from exc


def _content_text(data: dict[str, Any]) -> str:
    parts = data.get("content", [])
    return "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")


def _diagnostic_message(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:650]}"


def heuristic_observations(text: str) -> list[Observation]:
    observations: list[Observation] = []
    for sentence in _sentences(text):
        name = _candidate_soldier_id(sentence)
        if name is None:
            continue
        observations.append(
            Observation(
                soldier_id=name,
                task_code=_heuristic_task_code(sentence),
                note=sentence[:500],
                rating=_heuristic_rating(sentence),
                source="free_text",
            )
        )
    if not observations and text.strip():
        observations.append(
            Observation(
                soldier_id="UNKNOWN", task_code="UNMAPPED", note=text[:500], rating="UNCERTAIN"
            )
        )
    return observations


def heuristic_recommendations(observations: list[Observation]) -> list[ScenarioRecommendation]:
    return draft_intervention_recommendations(observations)


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in text.replace("\n", ". ").split(".") if item.strip()]


def _candidate_soldier_id(sentence: str) -> str | None:
    ignored = {
        "AAR",
        "AM",
        "Bird",
        "Cedar",
        "FRAGO",
        "GO",
        "MGRS",
        "MV",
        "NOGO",
        "OR",
        "PB",
        "Phase",
        "SITREP",
        "TC",
    }
    for token in sentence.replace(",", " ").split():
        candidate = token.strip(":;()[]{}")
        if not candidate or candidate in ignored:
            continue
        if candidate[0].isupper() and candidate.replace("-", "").isalnum():
            return candidate
    return None


def _heuristic_task_code(sentence: str) -> str:
    lowered = sentence.lower()
    if any(
        term in lowered
        for term in ("phase line", "sitrep", "frago", "movement", "checkpoint report")
    ):
        return "MV-2"
    if any(
        term in lowered
        for term in (
            "patrol-base",
            "patrol base",
            "priorities of work",
            "security",
            "asleep",
        )
    ):
        return "PB-7"
    if any(term in lowered for term in ("ambush", "fire control", "initiation")):
        return "AM-4"
    if any(term in lowered for term in ("delegat", "leader", "decision", "hesitated")):
        return "leadership"
    return "UNMAPPED"


def _heuristic_rating(sentence: str) -> Literal["GO", "NOGO", "UNCERTAIN"]:
    lowered = sentence.lower()
    negative_terms = (
        "blew",
        "missed",
        "failed",
        "no sitrep",
        "did not",
        "asleep",
        "late",
        "lost",
        "unsafe",
        "hesitated",
    )
    positive_terms = (
        "textbook",
        "correct",
        "solid",
        "good",
        "met standard",
        "passed",
        "clear",
        "concise",
        "delivered",
        "maintained",
    )
    if any(term in lowered for term in negative_terms):
        return "NOGO"
    if any(term in lowered for term in positive_terms):
        return "GO"
    return "UNCERTAIN"


def _observation_from_model_item(item: dict[str, Any]) -> Observation:
    rating = item.get("rating", "UNCERTAIN")
    if rating not in {"GO", "NOGO", "UNCERTAIN"}:
        rating = "UNCERTAIN"
    source = item.get("source", "free_text")
    if source not in {"audio", "image", "free_text", "synthetic"}:
        source = "free_text"
    confidence = item.get("confidence")
    if confidence is not None:
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None
    if confidence is not None and confidence < 0.55:
        rating = "UNCERTAIN"
    note = str(item.get("note") or "Model extracted an uncertain observation.")[:1200]
    task_code = str(item.get("task_code") or "UNMAPPED").strip() or "UNMAPPED"
    if task_code.upper() in {"UNKNOWN", "NONE", "N/A", "NA", "UNMAPPED"}:
        task_code = _heuristic_task_code(note)
    return Observation(
        soldier_id=str(item.get("soldier_id") or "UNKNOWN"),
        task_code=task_code,
        note=note,
        rating=rating,
        source=source,
        confidence=confidence,
    )


def _uncertainty_from_model_item(item: dict[str, Any], model: str) -> ExtractionUncertainty:
    raw_type = item.get("uncertainty_type")
    if raw_type not in {
        "ambiguous_text",
        "low_model_confidence",
        "ocr_low_confidence",
        "uncertain_rating",
        "unknown_soldier",
    }:
        raw_type = "ambiguous_text"
    uncertainty_type = cast(
        Literal[
            "ambiguous_text",
            "low_model_confidence",
            "ocr_low_confidence",
            "uncertain_rating",
            "unknown_soldier",
        ],
        raw_type,
    )
    confidence = item.get("confidence", 0.5)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5
    note = str(item.get("note") or "Model returned an unsupported uncertainty label.")[:700]
    return ExtractionUncertainty(
        source_ref=str(item.get("source_ref") or f"model://openai/{model}#uncertainty"),
        uncertainty_type=uncertainty_type,
        confidence=confidence,
        note=note,
        soldier_id=str(item["soldier_id"]) if item.get("soldier_id") is not None else None,
        task_code=str(item["task_code"]) if item.get("task_code") is not None else None,
    )


def _openai_json_object(
    *,
    client: Any,
    model: str,
    system_content: str,
    user_content: str,
) -> dict[str, Any]:
    try:
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
        )
        return json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:
        if not _should_retry_with_responses(exc):
            raise

    response = client.responses.create(
        model=model,
        instructions=system_content,
        input=user_content,
        text={"format": {"type": "json_object"}},
    )
    return json.loads(getattr(response, "output_text", "") or "{}")


def _should_retry_with_responses(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not a chat model" in message or "v1/chat/completions" in message

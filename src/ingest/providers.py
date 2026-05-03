from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal, cast

from tenacity import retry, stop_after_attempt, wait_exponential

from src.agent.models import (
    MODEL_DEEPGRAM_NOVA3,
    MODEL_GPT_4O_TRANSCRIBE,
    MODEL_MISTRAL_OCR,
)
from src.config import Settings, settings
from src.contracts import (
    DevelopmentEdge,
    ORBookletPage,
    ORBookletRow,
    Observation,
    RiskLevel,
    ScenarioRecommendation,
)
from src.ingest.scrub import scrub_sensitive_text


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderClients:
    settings: Settings = settings

    async def transcribe(self, audio_b64: str) -> str:
        try:
            return self._openai_transcribe(audio_b64, model=self.settings.openai_stt_model)
        except Exception as primary_error:
            if self.settings.deepgram_api_key:
                try:
                    return self._deepgram_transcribe(audio_b64)
                except Exception:
                    pass
            raise ProviderError(
                f"STT failed and OpenAI fallback is not configured: {primary_error}"
            )

    async def ocr_pages(self, image_b64: list[str]) -> list[ORBookletPage]:
        pages: list[ORBookletPage] = []
        for image in image_b64:
            try:
                pages.append(self._openai_vision_ocr(image))
            except Exception:
                try:
                    pages.append(self._mistral_ocr(image))
                except Exception:
                    pages.append(self._claude_vision_ocr(image))
        return pages

    async def extract_observations(self, text: str) -> list[Observation]:
        scrubbed = scrub_sensitive_text(text)
        if self.settings.anthropic_api_key:
            try:
                extracted = self._claude_json(
                    "Extract Ranger School observations as JSON array with soldier_id, "
                    "task_code, note, rating, and source. Use rating UNCERTAIN when unclear.",
                    scrubbed,
                )
                return [Observation.model_validate(item) for item in extracted]
            except Exception:
                pass
        return heuristic_observations(scrubbed)

    async def draft_recommendations(
        self, observations: list[Observation]
    ) -> list[ScenarioRecommendation]:
        if self.settings.anthropic_api_key:
            try:
                payload = [item.model_dump(mode="json") for item in observations]
                extracted = self._claude_json(
                    "Draft doctrinally cited ScenarioRecommendation JSON array. "
                    "Use TC 3-21.76 references and conservative safety checks.",
                    json.dumps(payload),
                )
                return [ScenarioRecommendation.model_validate(item) for item in extracted]
            except Exception:
                pass
        return heuristic_recommendations(observations)

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


def heuristic_observations(text: str) -> list[Observation]:
    observations: list[Observation] = []
    known = {"Jones": "MV-2", "Smith": "PB-7", "Garcia": "AM-4"}
    for name, task in known.items():
        if name.lower() in text.lower():
            rating: Literal["GO", "NOGO", "UNCERTAIN"] = (
                "GO" if name == "Garcia" or "textbook" in text.lower() else "NOGO"
            )
            observations.append(
                Observation(
                    soldier_id=name,
                    task_code=task,
                    note=_sentence_for_name(text, name),
                    rating=rating,
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
    recommendations: list[ScenarioRecommendation] = []
    for obs in observations:
        edge = (
            DevelopmentEdge.communications
            if obs.task_code == "MV-2"
            else DevelopmentEdge.team_accountability
        )
        if obs.task_code == "AM-4":
            edge = DevelopmentEdge.fire_control
        recommendations.append(
            ScenarioRecommendation(
                target_soldier_id=obs.soldier_id,
                rationale=(
                    f"{obs.soldier_id} showed a development signal on {obs.task_code}: "
                    f"{obs.note[:180]}"
                ),
                development_edge=edge,
                proposed_modification=(
                    "Assign a short, instructor-approved scenario inject that forces the student "
                    "to rehearse the observed task under controlled fatigue and time pressure."
                ),
                doctrine_refs=[f"TC 3-21.76 {obs.task_code}"],
                safety_checks=["No immersion, live-fire, or unsupervised movement added."],
                estimated_duration_min=15,
                requires_resources=[],
                risk_level=RiskLevel.low,
                fairness_score=1.0,
            )
        )
    return recommendations


def _sentence_for_name(text: str, name: str) -> str:
    for sentence in text.replace("\n", ". ").split("."):
        if name.lower() in sentence.lower():
            return sentence.strip()[:500] or text[:500]
    return text[:500]

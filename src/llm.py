"""Thin LLM wrapper. Provider is chosen with LLM_PROVIDER = gemini | anthropic | none.

`none` means agents fall back to their rule-based logic, so the pipeline still runs.
"""
from __future__ import annotations

import json
import logging
import re

from config.settings import Settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.llm_provider
        self._client = None

        if self.provider == "gemini" and settings.gemini_api_key:
            from google import genai  # pip install google-genai
            self._client = genai.Client(api_key=settings.gemini_api_key)
        elif self.provider == "anthropic" and settings.anthropic_api_key:
            import anthropic  # pip install anthropic
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        else:
            self.provider = "none"

    @property
    def available(self) -> bool:
        return self._client is not None

    def generate(self, system: str, prompt: str, temperature: float = 0.6) -> str:
        if not self.available:
            raise LLMUnavailable("No LLM configured")

        if self.provider == "gemini":
            from google.genai import types
            resp = self._client.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )
            return resp.text or ""

        resp = self._client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=1500,
            system=system,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    def generate_json(self, system: str, prompt: str, temperature: float = 0.6) -> dict:
        raw = self.generate(system + "\nRespond with a single JSON object only.", prompt, temperature)
        return parse_json(raw)


def parse_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in LLM output: {text[:200]}")
    return json.loads(match.group(0))

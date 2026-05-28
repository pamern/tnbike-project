"""Shared LLM client with Groq multi-key failover."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

from groq import Groq

from ai.common import load_environment, log_pending_issue, setup_logging


load_environment()
logger = setup_logging(__name__)


RETRYABLE_ERROR_MARKERS = (
    "rate limit",
    "rate_limit",
    "429",
    "503",
    "500",
    "overloaded",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "insufficient_quota",
    "memory",
    "oom",
)


def _split_keys(raw_value: str) -> list[str]:
    keys: list[str] = []
    for chunk in raw_value.replace("\n", ",").replace(";", ",").split(","):
        key = chunk.strip()
        if key:
            keys.append(key)
    return list(dict.fromkeys(keys))


def load_groq_keys() -> list[str]:
    raw_multi = os.getenv("GROQ_API_KEYS", "")
    raw_single = os.getenv("GROQ_API_KEY", "")
    keys = _split_keys(raw_multi)
    keys.extend(key for key in _split_keys(raw_single) if key not in keys)
    return keys


def strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def parse_json_text(text: str) -> dict[str, Any]:
    return json.loads(strip_json_fences(text))


def _is_retryable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in RETRYABLE_ERROR_MARKERS)


@dataclass
class GroqKeyPoolClient:
    keys: list[str] = field(default_factory=load_groq_keys)
    model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")))
    fallback_models: list[str] = field(
        default_factory=lambda: [
            model.strip()
            for model in os.getenv("GROQ_FALLBACK_MODELS", "llama-3.1-8b-instant").split(",")
            if model.strip()
        ]
    )
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "2000")))
    temperature: float = 0.2
    per_key_attempts: int = field(default_factory=lambda: int(os.getenv("GROQ_PER_KEY_ATTEMPTS", "1")))
    cooldown_seconds: int = field(default_factory=lambda: int(os.getenv("GROQ_KEY_COOLDOWN_SECONDS", "60")))
    _cooldowns: dict[str, float] = field(default_factory=dict)
    _cursor: int = 0

    def __post_init__(self) -> None:
        if self.keys:
            random.shuffle(self.keys)

    def has_keys(self) -> bool:
        return bool(self.keys)

    def _available_keys(self) -> list[str]:
        now = time.time()
        available = [key for key in self.keys if self._cooldowns.get(key, 0) <= now]
        return available or self.keys

    def _next_key(self) -> str:
        available = self._available_keys()
        key = available[self._cursor % len(available)]
        self._cursor += 1
        return key

    def _cooldown_key(self, key: str) -> None:
        self._cooldowns[key] = time.time() + self.cooldown_seconds

    def chat_text(self, prompt: str, system: str | None = None) -> str:
        if not self.keys:
            raise RuntimeError("No Groq API keys configured.")

        model_queue = [self.model, *[m for m in self.fallback_models if m != self.model]]
        attempts_total = max(len(self.keys) * max(self.per_key_attempts, 1), 1)
        last_error = ""

        for model in model_queue:
            for _ in range(attempts_total):
                key = self._next_key()
                client = Groq(api_key=key)
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        response_format={"type": "json_object"},
                    )
                    self.model = model
                    return response.choices[0].message.content or ""
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning("Groq request failed; rotating key/model. Error: %s", last_error)
                    if _is_retryable_error(exc):
                        self._cooldown_key(key)
                        continue
                    raise

        log_pending_issue(f"All Groq keys failed or are cooling down. Last error: {last_error}")
        raise RuntimeError(f"All Groq keys failed or are cooling down. Last error: {last_error}")

    def chat_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        text = self.chat_text(prompt=prompt, system=system)
        return parse_json_text(text)

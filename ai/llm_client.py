"""Shared LLM client with multi-provider key failover."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
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


PROVIDER_KEY_ENV = {
    "groq": ("GROQ_API_KEYS", "GROQ_API_KEY"),
    "openai": ("OPENAI_API_KEYS", "OPENAI_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY"),
}

PROVIDER_DEFAULT_MODEL = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "openrouter": "openai/gpt-4o-mini",
}

OPENAI_COMPATIBLE_BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "groq").strip().lower() or "groq"


def _provider_model(provider: str) -> str:
    provider_key = provider.upper()
    return os.getenv(
        f"{provider_key}_MODEL",
        os.getenv("LLM_MODEL", PROVIDER_DEFAULT_MODEL.get(provider, PROVIDER_DEFAULT_MODEL["groq"])),
    )


def load_provider_keys(provider: str | None = None) -> list[str]:
    provider = provider or _provider()
    multi_name, single_name = PROVIDER_KEY_ENV.get(provider, PROVIDER_KEY_ENV["groq"])
    raw_multi = os.getenv(multi_name, "")
    raw_single = os.getenv(single_name, "")
    keys = _split_keys(raw_multi)
    keys.extend(key for key in _split_keys(raw_single) if key not in keys)
    return keys


def load_groq_keys() -> list[str]:
    return load_provider_keys("groq")


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
    keys: list[str] = field(default_factory=lambda: load_provider_keys(_provider()))
    provider: str = field(default_factory=_provider)
    model: str = field(default_factory=lambda: _provider_model(_provider()))
    fallback_models: list[str] = field(default_factory=list)
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "2000")))
    temperature: float = 0.2
    per_key_attempts: int = field(default_factory=lambda: int(os.getenv("GROQ_PER_KEY_ATTEMPTS", "1")))
    cooldown_seconds: int = field(default_factory=lambda: int(os.getenv("GROQ_KEY_COOLDOWN_SECONDS", "60")))
    _cooldowns: dict[str, float] = field(default_factory=dict)
    _cursor: int = 0

    def __post_init__(self) -> None:
        self.provider = self.provider.strip().lower() or "groq"
        if not self.fallback_models:
            fallback_env = os.getenv(f"{self.provider.upper()}_FALLBACK_MODELS", "")
            if not fallback_env and self.provider == "groq":
                fallback_env = os.getenv("GROQ_FALLBACK_MODELS", "llama-3.1-8b-instant")
            self.fallback_models = [model.strip() for model in fallback_env.split(",") if model.strip()]
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

    def _chat_groq(self, key: str, model: str, messages: list[dict[str, str]]) -> str:
        client = Groq(api_key=key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    def _chat_openai_compatible(self, key: str, model: str, messages: list[dict[str, str]]) -> str:
        base_url = OPENAI_COMPATIBLE_BASE_URL[self.provider]
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if self.provider == "openrouter":
            headers.update({"HTTP-Referer": "http://localhost", "X-Title": "VIZOR"})
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=60) as client:
            response = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"] or ""

    def _chat_anthropic(self, key: str, model: str, messages: list[dict[str, str]], system: str | None) -> str:
        payload = {
            "model": model,
            "messages": [message for message in messages if message["role"] != "system"],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if system:
            payload["system"] = system + "\nChỉ trả JSON hợp lệ. Nếu có nội dung hiển thị cho người dùng, hãy viết bằng tiếng Việt chuyên nghiệp."
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=60) as client:
            response = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            response.raise_for_status()
            content = response.json().get("content", [])
            return "".join(block.get("text", "") for block in content if block.get("type") == "text")

    def chat_text(self, prompt: str, system: str | None = None) -> str:
        if not self.keys:
            raise RuntimeError(f"No {self.provider} API keys configured.")

        model_queue = [self.model, *[m for m in self.fallback_models if m != self.model]]
        attempts_total = max(len(self.keys) * max(self.per_key_attempts, 1), 1)
        last_error = ""

        for model in model_queue:
            for _ in range(attempts_total):
                key = self._next_key()
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                try:
                    if self.provider == "groq":
                        text = self._chat_groq(key, model, messages)
                    elif self.provider in OPENAI_COMPATIBLE_BASE_URL:
                        text = self._chat_openai_compatible(key, model, messages)
                    elif self.provider == "anthropic":
                        text = self._chat_anthropic(key, model, messages, system)
                    else:
                        raise RuntimeError(f"Unsupported LLM_PROVIDER={self.provider}")
                    self.model = model
                    return text
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning("%s request failed; rotating key/model. Error: %s", self.provider, last_error)
                    if _is_retryable_error(exc):
                        self._cooldown_key(key)
                        continue
                    raise

        log_pending_issue(f"All {self.provider} keys failed or are cooling down. Last error: {last_error}")
        raise RuntimeError(f"All {self.provider} keys failed or are cooling down. Last error: {last_error}")

    def chat_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        text = self.chat_text(prompt=prompt, system=system)
        return parse_json_text(text)

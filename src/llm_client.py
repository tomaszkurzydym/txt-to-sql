"""OpenAI-compatible Fireworks inference client."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

DEFAULT_BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_MODEL = "accounts/fireworks/models/kimi-k2p6"
# kimi-k2p6 is a reasoning model; keep headroom so content is not truncated to empty.
DEFAULT_MAX_TOKENS = 8192

FENCE_RE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)
SELECT_RE = re.compile(
    r"((?:WITH\b[\s\S]+?)?SELECT\b[\s\S]+?)(?:;|\Z)",
    re.IGNORECASE,
)


def get_model_name() -> str:
    return os.getenv("FIREWORKS_MODEL", DEFAULT_MODEL)


def create_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> OpenAI:
    """Create an OpenAI SDK client pointed at Fireworks."""
    key = api_key or os.getenv("FIREWORKS_API_KEY")
    if not key:
        raise RuntimeError(
            "FIREWORKS_API_KEY is not set. Export it or add it to a .env file."
        )
    return OpenAI(
        api_key=key,
        base_url=base_url or os.getenv("FIREWORKS_BASE_URL", DEFAULT_BASE_URL),
    )


def _extract_sql_candidate(text: str) -> str:
    """Pull SQL out of free-form / reasoning text when content is empty."""
    text = (text or "").strip()
    if not text:
        return ""
    fence = FENCE_RE.findall(text)
    if fence:
        # Prefer the last fenced block (often the final answer in a long think)
        return fence[-1].strip()
    matches = list(SELECT_RE.finditer(text))
    if matches:
        return matches[-1].group(1).strip()
    return text


class FireworksLLM:
    """Thin wrapper around Fireworks chat completions, reusable as the agent grows."""

    def __init__(
        self,
        client: Optional[OpenAI] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.client = client or create_client()
        self.model = model or get_model_name()
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """Send a chat completion request and return the assistant text.

        Reasoning models (e.g. kimi-k2p6) may put the chain-of-thought in
        `reasoning_content` and the final answer in `content`. If `content` is
        empty (often when max_tokens was too low), fall back to extracting SQL
        from reasoning_content.
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=self.max_tokens if max_tokens is None else max_tokens,
            **kwargs,
        )
        message = response.choices[0].message
        content = (message.content or "").strip()
        if content:
            return content

        reasoning = getattr(message, "reasoning_content", None) or ""
        if not reasoning:
            dump = message.model_dump() if hasattr(message, "model_dump") else {}
            reasoning = dump.get("reasoning_content") or ""
        extracted = _extract_sql_candidate(reasoning)
        return extracted.strip()

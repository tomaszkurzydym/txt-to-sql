"""OpenAI-compatible Fireworks inference client.

Wraps the OpenAI SDK pointed at Fireworks. Special-cases reasoning models
(e.g. kimi-k2p6) that may leave ``content`` empty and put the chain-of-thought
in ``reasoning_content`` — in that case we extract a SQL candidate from the
reasoning text. ``max_tokens`` is floored so reasoning models are not truncated
to an empty final answer.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

DEFAULT_BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_MODEL = "accounts/fireworks/models/kimi-k2p6"
# kimi-k2p6 is a reasoning model; keep headroom so content is not truncated.
DEFAULT_MAX_TOKENS = 8192
# Floor so callers cannot accidentally starve reasoning models of budget.
MIN_MAX_TOKENS = 1024

FENCE_RE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)
SELECT_RE = re.compile(
    r"((?:WITH\b[\s\S]+?)?SELECT\b[\s\S]+?)(?:;|\Z)",
    re.IGNORECASE,
)


def get_model_name() -> str:
    """Return the configured Fireworks model id.

    Returns:
        Model id from ``FIREWORKS_MODEL``, or ``DEFAULT_MODEL``.
    """
    return os.getenv("FIREWORKS_MODEL", DEFAULT_MODEL)


def create_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> OpenAI:
    """Create an OpenAI SDK client pointed at Fireworks.

    Args:
        api_key: Explicit API key. Defaults to ``FIREWORKS_API_KEY``.
        base_url: Inference base URL. Defaults to ``FIREWORKS_BASE_URL`` or
            ``DEFAULT_BASE_URL``.

    Returns:
        Configured ``OpenAI`` client instance.

    Raises:
        RuntimeError: If no API key is available.
    """
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
    """Pull SQL out of free-form / reasoning text when content is empty.

    Prefers the last fenced SQL block, then the last SELECT/WITH…SELECT match,
    else returns the stripped text.

    Args:
        text: Raw reasoning or free-form model output.

    Returns:
        Best-effort SQL string (may still need fence stripping by the agent).
    """
    text = (text or "").strip()
    if not text:
        return ""
    fence = FENCE_RE.findall(text)
    if fence:
        # Prefer the last fenced block (often the final answer in a long think).
        return fence[-1].strip()
    matches = list(SELECT_RE.finditer(text))
    if matches:
        return matches[-1].group(1).strip()
    return text


def _floor_max_tokens(max_tokens: int) -> int:
    """Enforce a minimum completion budget for reasoning models.

    Args:
        max_tokens: Requested max tokens.

    Returns:
        ``max(max_tokens, MIN_MAX_TOKENS)``.
    """
    return max(int(max_tokens), MIN_MAX_TOKENS)


class FireworksLLM:
    """Thin wrapper around Fireworks chat completions.

    Attributes:
        client: OpenAI SDK client aimed at Fireworks.
        model: Model id string.
        temperature: Default sampling temperature.
        max_tokens: Default completion budget (floored on each call).
    """

    def __init__(
        self,
        client: Optional[OpenAI] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        """Initialize the Fireworks chat wrapper.

        Args:
            client: Optional pre-built OpenAI client.
            model: Optional model override.
            temperature: Default sampling temperature.
            max_tokens: Default max tokens (subject to ``MIN_MAX_TOKENS`` floor).
        """
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
        ``reasoning_content`` and the final answer in ``content``. If
        ``content`` is empty (often when max_tokens was too low), fall back to
        extracting SQL from ``reasoning_content``.

        Args:
            messages: OpenAI-style chat messages.
            temperature: Optional per-call temperature override.
            max_tokens: Optional per-call max tokens (floored to
                ``MIN_MAX_TOKENS``).
            **kwargs: Extra kwargs forwarded to ``chat.completions.create``.

        Returns:
            Assistant text (or SQL extracted from reasoning when content is
            empty).
        """
        requested = self.max_tokens if max_tokens is None else max_tokens
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=_floor_max_tokens(requested),
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

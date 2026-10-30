import json
import logging
from typing import Any, Literal

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
# Re-runs a request declined by safety classifiers on Anthropic's recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ATTEMPTS = 2

Effort = Literal["low", "medium", "high"]


def structured_call(
    client: anthropic.Anthropic,
    *,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    effort: Effort,
    max_tokens: int = 16000,
) -> dict[str, Any] | None:
    """Ask Claude for JSON matching `schema`; returns None if no valid answer after a retry."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            # The system prompt is identical across calls in a run, so cache it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        if response.stop_reason != "end_turn":
            log.warning("Claude stopped with %s (attempt %d)", response.stop_reason, attempt)
            continue
        # After a fallback the last text block is the answer; earlier ones are discarded partials.
        texts = [block.text for block in response.content if block.type == "text"]
        try:
            return json.loads(texts[-1])
        except (IndexError, json.JSONDecodeError):
            log.warning("Claude returned no valid JSON (attempt %d)", attempt)
    return None

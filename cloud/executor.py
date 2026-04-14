"""EXECUTE — API calls and mode-pipeline routing.

Allowed mode pipelines per spec:
  STRIP → MODULE   (noise-reduced input, then structured output)
  STRIP → TOKEN    (noise-reduced input, then compact output)
  TOKEN → MODULE   (compressed, then structured — used in recovery)

Single API call uses a fixed system prompt per mode for determinism.
STRIP→MODULE upgrades the model to at least MEDIUM for the generation step.
"""

from __future__ import annotations

from collections.abc import Callable

import anthropic

from .selector import MODELS

# Fixed system prompts — stable keyword ordering enforces determinism.
_SYSTEM: dict[str, str] = {
    "TOKEN": (
        "SYSTEM: Cloud Plugin — TOKEN\n"
        "RULES:\n"
        "- ultra-compact output only\n"
        "- no explanation, no prose, no preamble\n"
        "- key:value format where possible\n"
        "- omit stop words\n"
        "- minimize tokens aggressively"
    ),
    "MODULE": (
        "SYSTEM: Cloud Plugin — MODULE\n"
        'OUTPUT: valid JSON matching this schema exactly:\n'
        '  {"module": "<string>", "layer": "<core|services|adapters|ui>", "code": "<string>"}\n'
        "ARCHITECTURE:\n"
        "  core: no external deps\n"
        "  services: imports core only\n"
        "  adapters: imports services + core\n"
        "  ui: no business logic\n"
        "RULES:\n"
        "- output ONLY the JSON object, nothing else\n"
        "- reject any response that violates schema or architecture"
    ),
    "STRIP": (
        "SYSTEM: Cloud Plugin — STRIP\n"
        "RULES:\n"
        "- extract and return: task, constraints, required data\n"
        "- remove: filler, repetition, conversational text, verbose phrasing\n"
        "- compact: sentences → key:value, collapse lists, remove stop words\n"
        "- output compacted context only — no generation, no explanation"
    ),
}

# When STRIP chains to MODULE, escalate from SMALL to MEDIUM for generation.
_STRIP_CHAIN_UPGRADE: dict[str, str] = {"SMALL": "MEDIUM"}


def _single_call(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model_key: str,
    max_tokens: int,
    constraints: str | None,
    on_token: Callable[[str], None] | None = None,
) -> str:
    """One API call: task → Claude → text response.

    If on_token is provided, streams the response and calls on_token for each
    text chunk. Always returns the full collected text.
    """
    user_parts = [f"TASK: {task}"]
    if constraints:
        user_parts.append(f"CONSTRAINTS: {constraints}")

    kwargs = dict(
        model=MODELS[model_key],
        max_tokens=max(max_tokens, 256),
        system=_SYSTEM[mode],
        messages=[{"role": "user", "content": "\n".join(user_parts)}],
    )

    if on_token is not None:
        chunks: list[str] = []
        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                on_token(text)
                chunks.append(text)
        return "".join(chunks)

    response = client.messages.create(**kwargs)
    return next((b.text for b in response.content if b.type == "text"), "")


def execute(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model_key: str,
    max_tokens: int,
    constraints: str | None,
    classification: dict,
    on_token: Callable[[str], None] | None = None,
) -> str:
    """EXECUTE with mode-pipeline routing.

    STRIP chains into MODULE or TOKEN depending on task type:
      type==format → STRIP → TOKEN
      else         → STRIP → MODULE

    When chaining STRIP→MODULE, the model is upgraded to at least MEDIUM
    so the structured generation step has enough capacity.

    on_token: if provided, streams the final generation step. The STRIP
    reduction pass is never streamed (it's an internal step).
    """
    if mode == "STRIP":
        # STRIP pass: always silent (internal context reduction)
        stripped_ctx = _single_call(
            client, task, "STRIP", model_key, max_tokens, constraints
        )
        next_mode = "TOKEN" if classification.get("type") == "format" else "MODULE"
        # Upgrade model for the generation step if we're producing structured output
        next_model = (
            _STRIP_CHAIN_UPGRADE.get(model_key, model_key)
            if next_mode == "MODULE"
            else model_key
        )
        return _single_call(
            client, stripped_ctx, next_mode, next_model, max_tokens, None, on_token
        )

    return _single_call(client, task, mode, model_key, max_tokens, constraints, on_token)

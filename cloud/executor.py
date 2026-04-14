"""EXECUTE — API calls and mode-pipeline routing.

Allowed mode pipelines per spec:
  STRIP → MODULE   (noise-reduced input, then structured output)
  STRIP → TOKEN    (noise-reduced input, then compact output)
  TOKEN → MODULE   (compressed, then structured — used in recovery)

Single API call uses a fixed system prompt per mode for determinism.
"""

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


def _single_call(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model_key: str,
    max_tokens: int,
    constraints: str | None,
) -> str:
    """One API call: task → Claude → text response."""
    user_parts = [f"TASK: {task}"]
    if constraints:
        user_parts.append(f"CONSTRAINTS: {constraints}")

    response = client.messages.create(
        model=MODELS[model_key],
        max_tokens=max(max_tokens, 256),
        system=_SYSTEM[mode],
        messages=[{"role": "user", "content": "\n".join(user_parts)}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")


def execute(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model_key: str,
    max_tokens: int,
    constraints: str | None,
    classification: dict,
) -> str:
    """EXECUTE with mode-pipeline routing.

    STRIP chains into MODULE or TOKEN depending on task type:
      type==format → STRIP → TOKEN
      else         → STRIP → MODULE
    """
    if mode == "STRIP":
        stripped_ctx = _single_call(client, task, "STRIP", model_key, max_tokens, constraints)
        next_mode = "TOKEN" if classification.get("type") == "format" else "MODULE"
        return _single_call(client, stripped_ctx, next_mode, model_key, max_tokens, None)

    return _single_call(client, task, mode, model_key, max_tokens, constraints)

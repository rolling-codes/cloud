"""VALIDATE and BUDGET_ENFORCE.

VALIDATE checks:
  - mode compliance (MODULE requires valid JSON schema)
  - schema validity (module, layer, code fields)
  - architecture rules (layer dependency constraints)
  - token budget (output within 1.5× max_tokens)

BUDGET_ENFORCE trims output that exceeds budget after retries.

Failure conditions per spec:
  - token overflow
  - invalid schema
  - unstripped context (empty output)
  - architecture violation
  - nondeterministic output (empty)
"""

import json
import re

# Valid layer values — stable ordering.
_VALID_LAYERS: frozenset[str] = frozenset(("core", "services", "adapters", "ui"))

# Architecture violation patterns per layer.
_ARCH_RULES: dict[str, re.Pattern] = {
    "core": re.compile(r"\bimport\b.*(service|adapter|ui)", re.IGNORECASE),
    "ui":   re.compile(r"\b(db|database|sql|query|repository|model)\b", re.IGNORECASE),
}

_BUDGET_OVERFLOW_RATIO = 1.5  # output tokens may exceed max by this factor before failing


def validate_output(text: str, mode: str, max_tokens: int) -> tuple[bool, str]:
    """Return (valid, reason).

    reason is "ok" on success, or a short failure code on failure.
    """
    if not text.strip():
        return False, "empty_output"

    if mode == "MODULE":
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            return False, "schema:missing_json"
        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return False, "schema:invalid_json"

        if not {"module", "layer", "code"}.issubset(data.keys()):
            return False, "schema:missing_fields"
        if data.get("layer") not in _VALID_LAYERS:
            return False, "schema:invalid_layer"
        if not all(isinstance(data.get(k), str) for k in ("module", "code")):
            return False, "schema:invalid_types"

        # Architecture rules
        layer = data.get("layer", "")
        code = data.get("code", "")
        rule = _ARCH_RULES.get(layer)
        if rule and rule.search(code):
            return False, f"arch:violation({layer})"

    out_tokens = max(1, len(text) // 4)
    if out_tokens > max_tokens * _BUDGET_OVERFLOW_RATIO:
        return False, f"budget:overflow({out_tokens}>{max_tokens})"

    return True, "ok"


def enforce_budget(text: str, max_tokens: int) -> str:
    """BUDGET_ENFORCE — hard trim if still over limit after retries.

    Cuts at last newline within the char limit to avoid mid-line truncation.
    Appends [BUDGET:truncated] marker.
    """
    out_tokens = max(1, len(text) // 4)
    if out_tokens <= max_tokens:
        return text

    char_limit = max_tokens * 4
    trimmed = text[:char_limit]
    last_nl = trimmed.rfind("\n")
    if last_nl > char_limit * 0.8:
        trimmed = trimmed[:last_nl]

    return trimmed + "\n[BUDGET:truncated]"

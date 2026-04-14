"""MODE_SELECT and MODEL_SELECT.

Mode selection:
  noise==high      → STRIP  (then chains to MODULE or TOKEN)
  type==format     → TOKEN
  over_budget      → TOKEN  (force compact)
  else             → MODULE

Model selection (lightweight-first per spec):
  default start    → SMALL
  TOKEN, STRIP     → SMALL
  MODULE           → MEDIUM
  refactor, large  → LARGE

Escalation order: SMALL → MEDIUM → LARGE
"""

MODEL_ORDER: tuple[str, ...] = ("SMALL", "MEDIUM", "LARGE")

# Maps abstract tier to concrete model ID.
MODELS: dict[str, str] = {
    "SMALL":  "claude-haiku-4-5",
    "MEDIUM": "claude-sonnet-4-6",
    "LARGE":  "claude-opus-4-6",
}

_AUTO_VALUES = frozenset(("AUTO", ""))


def select_mode(cls: dict, requested: str, over_budget: bool) -> str:
    """Return execution mode string."""
    if requested not in _AUTO_VALUES:
        return requested
    if over_budget:
        return "TOKEN"
    if cls["noise"] == "high":
        return "STRIP"
    if cls["type"] == "format":
        return "TOKEN"
    return "MODULE"


def select_model(mode: str, cls: dict, requested: str) -> str:
    """Return model tier key (SMALL | MEDIUM | LARGE).

    Spec default: start SMALL, escalate for complexity.
      TOKEN  → SMALL
      STRIP  → SMALL
      MODULE → MEDIUM
      refactor or large → LARGE
    """
    if requested not in _AUTO_VALUES:
        return requested
    if cls["type"] == "refactor" or cls["size"] == "large":
        return "LARGE"
    if mode == "MODULE":
        return "MEDIUM"
    return "SMALL"


def escalate_model(current: str) -> str | None:
    """Return next model tier, or None if already at LARGE."""
    idx = MODEL_ORDER.index(current)
    if idx < len(MODEL_ORDER) - 1:
        return MODEL_ORDER[idx + 1]
    return None

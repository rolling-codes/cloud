"""CLASSIFY — task type, size, and noise classification.

type:  format | generate | refactor | analyze
size:  small | medium | large
noise: low | high

All keyword sets use fixed stable ordering for determinism.
"""

# Type keywords — checked in priority order (first match wins).
_TYPE_FORMAT: tuple[str, ...] = (
    "format", "indent", "style", "lint", "sort", "align",
)
_TYPE_REFACTOR: tuple[str, ...] = (
    "refactor", "rewrite", "restructure", "rename", "reorganize",
)
_TYPE_ANALYZE: tuple[str, ...] = (
    "analyze", "explain", "review", "audit", "check", "describe", "summarize",
)

# Noise signals.
_NOISE_WORDS: tuple[str, ...] = (
    "please", "could", "would", "just", "maybe",
    "think", "want", "need", "feel",
)
_NOISE_THRESHOLD = 3  # hits >= threshold → high noise

# Size thresholds (token count).
_SIZE_SMALL_MAX = 50
_SIZE_MEDIUM_MAX = 300


def classify_task(task: str) -> dict:
    """Classify a stripped task string.

    Returns:
        {"type": str, "size": str, "noise": str}
    """
    t = task.lower()

    # Type — priority order
    if any(kw in t for kw in _TYPE_FORMAT):
        task_type = "format"
    elif any(kw in t for kw in _TYPE_REFACTOR):
        task_type = "refactor"
    elif any(kw in t for kw in _TYPE_ANALYZE):
        task_type = "analyze"
    else:
        task_type = "generate"

    # Size by approximate token count
    tokens = max(1, len(task) // 4)
    if tokens < _SIZE_SMALL_MAX:
        size = "small"
    elif tokens < _SIZE_MEDIUM_MAX:
        size = "medium"
    else:
        size = "large"

    # Noise
    noise_hits = sum(1 for w in _NOISE_WORDS if w in t)
    noise = "high" if noise_hits >= _NOISE_THRESHOLD else "low"

    return {"type": task_type, "size": size, "noise": noise}

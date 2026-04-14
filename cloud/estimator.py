"""ESTIMATE — pre-execution token and complexity estimation.

Produces:
  tokens_in       rough input token count (~4 chars/token)
  tokens_out_pred predicted output token count
  complexity_score 0.0–1.0 based on keyword signals
  over_budget     True if tokens_out_pred > max_tokens
"""

# Fixed complexity signals — stable ordering.
_COMPLEXITY_KEYWORDS: tuple[str, ...] = (
    "refactor",
    "rewrite",
    "architecture",
    "system",
    "pipeline",
    "integrate",
    "migrate",
    "redesign",
    "optimize",
    "scale",
)

_COMPLEXITY_DENOMINATOR = 3  # hits needed to reach score 1.0


def estimate(task: str, max_tokens: int) -> dict:
    """Return estimation dict for task against token budget."""
    tokens_in = max(1, len(task) // 4)

    t = task.lower()
    hits = sum(1 for kw in _COMPLEXITY_KEYWORDS if kw in t)
    complexity_score = round(min(1.0, hits / _COMPLEXITY_DENOMINATOR), 2)

    # Output multiplier: 2× base + up to 2× extra for high complexity.
    multiplier = 2.0 + complexity_score * 2.0
    tokens_out_pred = int(tokens_in * multiplier)

    return {
        "tokens_in": tokens_in,
        "tokens_out_pred": tokens_out_pred,
        "complexity_score": complexity_score,
        "over_budget": tokens_out_pred > max_tokens,
    }

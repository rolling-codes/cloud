"""Session state — cache, delta processing, and snapshots.

Per spec:
  CACHE:     parsed_task + constraints → validated_output
             reuse if input == previous_input
  DELTA:     process only (new_input - previous_input), merge previous_output
  SNAPSHOTS: {step, output, validation} per execution step
             on failure → resume from last valid snapshot
  COLD START: True until first successful execution
"""

import hashlib


class SessionState:
    """Tracks cache, delta, and snapshots across interactive turns."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self.prev_input: str | None = None
        self.prev_output: str | None = None
        self.snapshots: list[dict] = []
        self.cold: bool = True  # True until first successful execution

    # ── Cache ──────────────────────────────────────────────────────────────────

    def _key(self, task: str, constraints: str | None) -> str:
        raw = f"{task}||{constraints or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def cache_get(self, task: str, constraints: str | None) -> str | None:
        """Return cached output or None."""
        return self._cache.get(self._key(task, constraints))

    def cache_put(self, task: str, constraints: str | None, output: str) -> None:
        """Store validated output in cache."""
        self._cache[self._key(task, constraints)] = output

    # ── Delta ──────────────────────────────────────────────────────────────────

    def input_delta(self, new_input: str) -> str:
        """Return only lines in new_input that were not in prev_input.

        Returns new_input unchanged if no prior input or completely new.
        """
        if self.prev_input is None:
            return new_input
        if new_input == self.prev_input:
            return ""
        prev_lines = set(self.prev_input.splitlines())
        delta = [l for l in new_input.splitlines() if l not in prev_lines]
        return "\n".join(delta) if delta else new_input

    # ── Snapshots ──────────────────────────────────────────────────────────────

    def snapshot(self, step: int, output: str, validation: str) -> None:
        """Record a pipeline step result."""
        self.snapshots.append({
            "step": step,
            "output": output,
            "validation": validation,
        })

    def last_valid_snapshot(self) -> dict | None:
        """Return the most recent snapshot with validation=="ok", or None."""
        valid = [s for s in self.snapshots if s["validation"] == "ok"]
        return valid[-1] if valid else None

    # ── State update ───────────────────────────────────────────────────────────

    def commit(self, task: str, constraints: str | None, output: str) -> None:
        """Called after a successful pipeline run."""
        self.cache_put(task, constraints, output)
        self.prev_input = task
        self.prev_output = output
        self.cold = False

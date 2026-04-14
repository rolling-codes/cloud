"""Main pipeline orchestrator.

Runs the full Cloud pipeline:
  INPUT → CONTEXT_SELECT → STRIP → ESTIMATE → CLASSIFY →
          MODE_SELECT → MODEL_SELECT → EXECUTE → VALIDATE →
          BUDGET_ENFORCE → OUTPUT

Failure recovery per spec:
  retry 1 → escalate MODEL
  retry 2 → force STRIP + TOKEN (two-step degrade)
  retry 3 → last valid snapshot OR minimal fallback
"""

import anthropic

from .parser import parse_command
from .stripper import strip_local
from .estimator import estimate
from .classifier import classify_task
from .selector import select_mode, select_model, escalate_model, MODELS
from .executor import execute
from .validator import validate_output, enforce_budget
from .session import SessionState

_MAX_RETRIES = 2


def run_pipeline(
    client: anthropic.Anthropic,
    raw_input: str,
    state: SessionState | None = None,
    verbose: bool = True,
) -> str:
    """Run the full Cloud pipeline on raw_input.

    Args:
        client:    Anthropic SDK client.
        raw_input: Raw user input (command format or plain text).
        state:     Optional session state for cache/delta/snapshots.
                   Pass None for stateless (piped/args) mode.
        verbose:   Print pipeline diagnostics.

    Returns:
        Final output string.
    """

    # ── CONTEXT_SELECT ─────────────────────────────────────────────────────────
    cmd = parse_command(raw_input)
    task: str = cmd["task"]
    max_tokens: int = cmd["max_tokens"]
    constraints: str | None = cmd["constraints"]

    # Cache check (session mode only)
    if state is not None:
        cached = state.cache_get(task, constraints)
        if cached is not None:
            if verbose:
                print("CACHE: hit")
            return cached

    # Delta processing (session mode, not cold start)
    if state is not None and not state.cold and state.prev_input is not None:
        delta = state.input_delta(task)
        if not delta:
            if verbose:
                print("DELTA: no change")
            return state.prev_output or ""
        task = delta

    # ── STRIP ──────────────────────────────────────────────────────────────────
    task = strip_local(task)

    # ── ESTIMATE ───────────────────────────────────────────────────────────────
    est = estimate(task, max_tokens)

    # ── CLASSIFY ───────────────────────────────────────────────────────────────
    cls = classify_task(task)

    # ── MODE_SELECT ────────────────────────────────────────────────────────────
    mode = select_mode(cls, cmd["mode"], est["over_budget"])

    # ── MODEL_SELECT ───────────────────────────────────────────────────────────
    model_key = select_model(mode, cls, cmd["model"])
    if model_key not in MODELS:
        model_key = "SMALL"

    if verbose:
        print("PIPELINE:")
        print(f"  classify : type={cls['type']} size={cls['size']} noise={cls['noise']}")
        print(
            f"  estimate : in=~{est['tokens_in']} "
            f"out_pred=~{est['tokens_out_pred']} "
            f"complexity={est['complexity_score']}"
        )
        print(f"  mode     : {mode}")
        print(f"  model    : {model_key} → {MODELS[model_key]}")
        print(f"  budget   : {max_tokens} tokens")
        print()

    # ── EXECUTE → VALIDATE → BUDGET_ENFORCE (with failure recovery) ────────────
    current_mode = mode
    current_model = model_key
    step = 0

    for retry in range(_MAX_RETRIES + 1):
        step += 1

        try:
            output = execute(client, task, current_mode, current_model, max_tokens, constraints, cls)
        except anthropic.APIError as e:
            return f"ERROR: {e}"

        valid, reason = validate_output(output, current_mode, max_tokens)

        if state is not None:
            state.snapshot(step, output, reason if valid else f"FAIL:{reason}")

        if valid:
            output = enforce_budget(output, max_tokens)
            if state is not None:
                state.commit(task, constraints, output)
            return output

        # Failure recovery
        if retry == 0:
            # retry 1: escalate model
            next_model = escalate_model(current_model)
            if next_model:
                current_model = next_model
                if verbose:
                    print(f"RECOVERY 1: model escalation → {current_model} [{reason}]")
            else:
                if verbose:
                    print(f"RECOVERY 1: already at LARGE [{reason}]")

        elif retry == 1:
            # retry 2: force STRIP + TOKEN (two-step degrade)
            current_mode = "STRIP"
            if verbose:
                print(f"RECOVERY 2: forcing STRIP+TOKEN [{reason}]")

    # All retries exhausted — resume from last valid snapshot if available
    if state is not None:
        snap = state.last_valid_snapshot()
        if snap:
            if verbose:
                print("FALLBACK: last valid snapshot")
            return snap["output"]

    if verbose:
        print(f"FALLBACK: minimal output [{reason}]")
    return f"RESULT: {task[:300]}"

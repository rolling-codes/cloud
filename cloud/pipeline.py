"""Main pipeline orchestrator.

Runs the full Cloud pipeline:
  INPUT → CONTEXT_SELECT → STRIP → ESTIMATE → CLASSIFY →
          MODE_SELECT → MODEL_SELECT → EXECUTE → VALIDATE →
          BUDGET_ENFORCE → OUTPUT

Failure recovery per spec:
  retry 1 → escalate MODEL
  retry 2 → force STRIP + TOKEN (two-step degrade)
  retry 3 → last valid snapshot OR minimal fallback

Rate limiting: exponential backoff (1s, 2s) on RateLimitError before retry.
Streaming: first attempt streams output to stdout; retries are silent.
"""

from __future__ import annotations

import time

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
_RATE_LIMIT_BACKOFF = (1, 2)  # seconds per retry attempt


# ── Helpers ────────────────────────────────────────────────────────────────────

def _stream_token(text: str) -> None:
    print(text, end="", flush=True)


def _print_diagnostics(cls: dict, est: dict, mode: str, model_key: str, budget: int) -> None:
    print("PIPELINE:")
    print(f"  classify : type={cls['type']} size={cls['size']} noise={cls['noise']}")
    print(
        f"  estimate : in=~{est['tokens_in']} "
        f"out_pred=~{est['tokens_out_pred']} "
        f"complexity={est['complexity_score']}"
    )
    print(f"  mode     : {mode}")
    print(f"  model    : {model_key} → {MODELS[model_key]}")
    print(f"  budget   : {budget} tokens")
    print()


def _attempt(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model: str,
    max_tokens: int,
    constraints: str | None,
    cls: dict,
    use_stream: bool,
) -> tuple[str | None, str | None]:
    """Run one execute attempt. Returns (output, rate_limit_error) tuple."""
    on_token = _stream_token if use_stream else None
    try:
        output = execute(client, task, mode, model, max_tokens, constraints, cls, on_token)
        if use_stream:
            print()
        return output, None
    except anthropic.RateLimitError as e:
        return None, str(e)


def _recover(retry: int, mode: str, model: str, reason: str, verbose: bool) -> tuple[str, str]:
    """Return (next_mode, next_model) after a validation failure."""
    if retry == 0:
        next_model = escalate_model(model) or model
        if verbose:
            if next_model != model:
                print(f"RECOVERY 1: model escalation → {next_model} [{reason}]")
            else:
                print(f"RECOVERY 1: already at LARGE [{reason}]")
        return mode, next_model
    # retry == 1: force STRIP+TOKEN
    if verbose:
        print(f"RECOVERY 2: forcing STRIP+TOKEN [{reason}]")
    return "STRIP", model


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(
    client: anthropic.Anthropic,
    raw_input: str,
    state: SessionState | None = None,
    verbose: bool = True,
    stream: bool = False,
) -> str:
    """Run the full Cloud pipeline on raw_input.

    Args:
        client:    Anthropic SDK client.
        raw_input: Raw user input (command format or plain text).
        state:     Optional session state for cache/delta/snapshots.
                   Pass None for stateless (piped/args) mode.
        verbose:   Print pipeline diagnostics.
        stream:    Stream first-attempt output to stdout as it arrives.
                   Retries are never streamed.

    Returns:
        Final output string.
    """
    cmd = parse_command(raw_input)
    task: str = cmd["task"]
    max_tokens: int = cmd["max_tokens"]
    constraints: str | None = cmd["constraints"]

    # ── CONTEXT_SELECT ─────────────────────────────────────────────────────────
    if state is not None:
        cached = state.cache_get(task, constraints)
        if cached is not None:
            if verbose:
                print("CACHE: hit")
            return cached

        if not state.cold and state.prev_input is not None:
            delta = state.input_delta(task)
            if not delta:
                if verbose:
                    print("DELTA: no change")
                return state.prev_output or ""
            task = delta

    # ── STRIP → ESTIMATE → CLASSIFY → SELECT ──────────────────────────────────
    task = strip_local(task)
    est = estimate(task, max_tokens)
    cls = classify_task(task)
    mode = select_mode(cls, cmd["mode"], est["over_budget"])
    model_key = select_model(mode, cls, cmd["model"])
    if model_key not in MODELS:
        model_key = "SMALL"

    if verbose:
        _print_diagnostics(cls, est, mode, model_key, max_tokens)

    # ── EXECUTE → VALIDATE → BUDGET_ENFORCE (with failure recovery) ────────────
    current_mode = mode
    current_model = model_key
    reason = "unknown"

    for retry in range(_MAX_RETRIES + 1):
        output, rate_err = _attempt(
            client, task, current_mode, current_model, max_tokens,
            constraints, cls, use_stream=(stream and retry == 0),
        )

        if rate_err is not None:
            wait = _RATE_LIMIT_BACKOFF[min(retry, len(_RATE_LIMIT_BACKOFF) - 1)]
            if verbose:
                print(f"RATE_LIMIT: waiting {wait}s before retry {retry + 1}")
            time.sleep(wait)
            continue

        if output is None:
            continue

        valid, reason = validate_output(output, current_mode, max_tokens)
        if state is not None:
            state.snapshot(retry + 1, output, reason if valid else f"FAIL:{reason}")

        if valid:
            output = enforce_budget(output, max_tokens)
            if state is not None:
                state.commit(task, constraints, output)
            return output

        current_mode, current_model = _recover(retry, current_mode, current_model, reason, verbose)

    # ── FALLBACK ───────────────────────────────────────────────────────────────
    if state is not None:
        snap = state.last_valid_snapshot()
        if snap:
            if verbose:
                print("FALLBACK: last valid snapshot")
            return snap["output"]

    if verbose:
        print(f"FALLBACK: minimal output [{reason}]")
    return f"RESULT: {task[:300]}"

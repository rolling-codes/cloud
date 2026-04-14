#!/usr/bin/env python3
"""
Cloud — deterministic execution control layer for Claude.

Pipeline: INPUT → CONTEXT_SELECT → STRIP → ESTIMATE → CLASSIFY →
          MODE_SELECT → MODEL_SELECT → EXECUTE → VALIDATE →
          BUDGET_ENFORCE → OUTPUT

Usage:
  python cloud.py                          # interactive session
  python cloud.py "TASK: refactor auth"   # single task via args
  echo "TASK: ..." | python cloud.py      # piped input

Command format (any order, all optional except TASK):
  TASK: <description>
  MODE: AUTO | TOKEN | MODULE | STRIP
  MODEL: AUTO | SMALL | MEDIUM | LARGE
  MAX_TOKENS: <n>
  CONSTRAINTS: <text>
"""

import sys
import json
import re
import hashlib

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic SDK not installed. Run: pip install anthropic")
    sys.exit(1)

# ── Model tiers (lightweight-first) ───────────────────────────────────────────

MODELS = {
    "SMALL":  "claude-haiku-4-5",
    "MEDIUM": "claude-sonnet-4-6",
    "LARGE":  "claude-opus-4-6",
}

MODEL_ORDER = ["SMALL", "MEDIUM", "LARGE"]

DEFAULT_MAX_TOKENS = 800

# ── System prompts (fixed keywords, stable ordering — determinism lock) ────────

_SYS_TOKEN = (
    "SYSTEM: Cloud Plugin — TOKEN\n"
    "RULES:\n"
    "- ultra-compact output only\n"
    "- no explanation, no prose, no preamble\n"
    "- key:value format where possible\n"
    "- omit stop words\n"
    "- minimize tokens aggressively"
)

_SYS_MODULE = (
    "SYSTEM: Cloud Plugin — MODULE\n"
    "OUTPUT: valid JSON matching this schema exactly:\n"
    '  {"module": "<string>", "layer": "<core|services|adapters|ui>", "code": "<string>"}\n'
    "ARCHITECTURE:\n"
    "  core: no external deps\n"
    "  services: imports core only\n"
    "  adapters: imports services + core\n"
    "  ui: no business logic\n"
    "RULES:\n"
    "- output ONLY the JSON object, nothing else\n"
    "- reject any response that violates schema or architecture"
)

_SYS_STRIP = (
    "SYSTEM: Cloud Plugin — STRIP\n"
    "RULES:\n"
    "- extract and return: task, constraints, required data\n"
    "- remove: filler, repetition, conversational text, verbose phrasing\n"
    "- compact: sentences → key:value, collapse lists, remove stop words\n"
    "- output compacted context only — no generation, no explanation"
)

SYSTEM_PROMPTS = {
    "TOKEN":  _SYS_TOKEN,
    "MODULE": _SYS_MODULE,
    "STRIP":  _SYS_STRIP,
}

# Fixed filler patterns — stable ordering enforced
_FILLER_PATTERNS = (
    r"\bplease\b",
    r"\bcould you\b",
    r"\bwould you\b",
    r"\bjust\b",
    r"\bkindly\b",
    r"\bi want you to\b",
    r"\bi need you to\b",
    r"\bi think\b",
    r"\bmaybe\b",
    r"\bbasically\b",
    r"\bactually\b",
    r"\bfeel free to\b",
    r"\bif you can\b",
    r"\bif possible\b",
)

# ── Session state ──────────────────────────────────────────────────────────────

class SessionState:
    """Tracks cache, delta, and snapshots across interactive turns."""

    def __init__(self):
        self.cache: dict[str, str] = {}       # input_hash → validated_output
        self.prev_input: str | None = None
        self.prev_output: str | None = None
        self.snapshots: list[dict] = []        # [{step, output, validation}]
        self.cold: bool = True                 # True until first successful execution

    def input_hash(self, task: str, constraints: str | None) -> str:
        key = f"{task}||{constraints or ''}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def cache_get(self, task: str, constraints: str | None) -> str | None:
        return self.cache.get(self.input_hash(task, constraints))

    def cache_put(self, task: str, constraints: str | None, output: str) -> None:
        self.cache[self.input_hash(task, constraints)] = output

    def input_delta(self, new_input: str) -> str:
        """Return only the new portion of input vs previous."""
        if self.prev_input is None:
            return new_input
        if new_input == self.prev_input:
            return ""
        # Simple delta: lines not in previous input
        prev_lines = set(self.prev_input.splitlines())
        new_lines = new_input.splitlines()
        delta = [l for l in new_lines if l not in prev_lines]
        return "\n".join(delta) if delta else new_input

    def snapshot(self, step: int, output: str, validation: str) -> None:
        self.snapshots.append({"step": step, "output": output, "validation": validation})

    def last_valid_snapshot(self) -> dict | None:
        valid = [s for s in self.snapshots if s["validation"] == "ok"]
        return valid[-1] if valid else None

# ── Pipeline stages ────────────────────────────────────────────────────────────

def parse_command(text: str) -> dict:
    """CONTEXT_SELECT — parse command interface. Extract task, constraints,
    required_prior_outputs. Ignore everything else."""
    result = {
        "task": None,
        "mode": "AUTO",
        "model": "AUTO",
        "max_tokens": DEFAULT_MAX_TOKENS,
        "constraints": None,
    }
    found_key = False
    for line in text.strip().splitlines():
        s = line.strip()
        u = s.upper()
        if u.startswith("TASK:"):
            result["task"] = s[5:].strip()
            found_key = True
        elif u.startswith("MODE:"):
            val = s[5:].strip().upper()
            if val in ("AUTO", "TOKEN", "MODULE", "STRIP"):
                result["mode"] = val
            found_key = True
        elif u.startswith("MODEL:"):
            val = s[6:].strip().upper()
            if val in ("AUTO", "SMALL", "MEDIUM", "LARGE"):
                result["model"] = val
            found_key = True
        elif u.startswith("MAX_TOKENS:"):
            try:
                result["max_tokens"] = int(s[11:].strip())
            except ValueError:
                pass
            found_key = True
        elif u.startswith("CONSTRAINTS:"):
            result["constraints"] = s[12:].strip()
            found_key = True

    if not found_key or result["task"] is None:
        result["task"] = text.strip()

    return result


def strip_local(text: str) -> str:
    """STRIP — local fast pass. Remove filler, compact context.
    Always on; rerun before output if over budget."""
    result = text
    for pattern in _FILLER_PATTERNS:  # stable ordering
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    # Collapse whitespace
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def estimate(task: str, max_tokens: int) -> dict:
    """ESTIMATE — tokens_in, tokens_out_pred, complexity_score."""
    tokens_in = max(1, len(task) // 4)

    # Complexity signals — stable keyword list
    complex_keywords = (
        "refactor", "rewrite", "architecture", "system", "pipeline",
        "integrate", "migrate", "redesign", "optimize", "scale",
    )
    complexity_hits = sum(1 for w in complex_keywords if w in task.lower())
    complexity_score = min(1.0, complexity_hits / 3.0)  # 0.0–1.0

    # Predict output: base multiplier + complexity boost
    multiplier = 2.0 + complexity_score * 2.0
    tokens_out_pred = int(tokens_in * multiplier)

    return {
        "tokens_in": tokens_in,
        "tokens_out_pred": tokens_out_pred,
        "complexity_score": round(complexity_score, 2),
        "over_budget": tokens_out_pred > max_tokens,
    }


def classify_task(task: str) -> dict:
    """CLASSIFY — type, size, noise. Fixed keyword sets, stable ordering."""
    t = task.lower()

    # Type — checked in fixed priority order
    if any(w in t for w in ("format", "indent", "style", "lint", "sort", "align")):
        task_type = "format"
    elif any(w in t for w in ("refactor", "rewrite", "restructure", "rename", "reorganize")):
        task_type = "refactor"
    elif any(w in t for w in ("analyze", "explain", "review", "audit", "check", "describe", "summarize")):
        task_type = "analyze"
    else:
        task_type = "generate"

    # Size
    tokens = max(1, len(task) // 4)
    if tokens < 50:
        size = "small"
    elif tokens < 300:
        size = "medium"
    else:
        size = "large"

    # Noise
    noise_words = ("please", "could", "would", "just", "maybe", "think", "want", "need", "feel")
    noise_count = sum(1 for w in noise_words if w in t)
    noise = "high" if noise_count >= 3 else "low"

    return {"type": task_type, "size": size, "noise": noise}


def select_mode(cls: dict, requested: str, over_budget: bool) -> str:
    """MODE_SELECT."""
    if requested not in ("AUTO", ""):
        return requested
    if over_budget:
        return "TOKEN"
    if cls["noise"] == "high":
        return "STRIP"
    if cls["type"] == "format":
        return "TOKEN"
    return "MODULE"


def select_model(mode: str, cls: dict, requested: str) -> str:
    """MODEL_SELECT — start SMALL, escalate for complexity.
    TOKEN→SMALL, STRIP→SMALL, MODULE→MEDIUM.
    refactor or large→LARGE."""
    if requested not in ("AUTO", ""):
        return requested
    if cls["type"] == "refactor" or cls["size"] == "large":
        return "LARGE"
    if mode == "MODULE":
        return "MEDIUM"
    return "SMALL"  # TOKEN and STRIP default to SMALL


def api_call(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model_key: str,
    max_tokens: int,
    constraints: str | None,
) -> str:
    """EXECUTE — single API call. temperature not exposed by messages API
    but determinism is enforced via fixed system prompts and stable inputs."""
    model_id = MODELS[model_key]
    system = SYSTEM_PROMPTS[mode]

    user_parts = [f"TASK: {task}"]
    if constraints:
        user_parts.append(f"CONSTRAINTS: {constraints}")

    response = client.messages.create(
        model=model_id,
        max_tokens=max(max_tokens, 256),
        system=system,
        messages=[{"role": "user", "content": "\n".join(user_parts)}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")


def execute_mode_pipeline(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model_key: str,
    max_tokens: int,
    constraints: str | None,
    cls: dict,
) -> str:
    """EXECUTE with mode pipelining support.

    Allowed pipelines per spec:
      STRIP → MODULE
      STRIP → TOKEN
      TOKEN → MODULE (compressed)

    STRIP mode runs first to reduce context, then chains into
    MODULE or TOKEN based on task type.
    """
    if mode == "STRIP":
        # Step 1: STRIP — reduce context
        stripped_ctx = api_call(client, task, "STRIP", model_key, max_tokens, constraints)
        # Step 2: chain into MODULE or TOKEN based on type
        next_mode = "MODULE" if cls["type"] not in ("format",) else "TOKEN"
        return api_call(client, stripped_ctx, next_mode, model_key, max_tokens, None)

    return api_call(client, task, mode, model_key, max_tokens, constraints)


def validate_output(text: str, mode: str, max_tokens: int) -> tuple[bool, str]:
    """VALIDATE — mode compliance, schema validity, token budget,
    architecture rules."""
    if not text.strip():
        return False, "empty_output"

    if mode == "MODULE":
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return False, "schema:missing_json"
            data = json.loads(match.group())
            if not {"module", "layer", "code"}.issubset(data.keys()):
                return False, "schema:missing_fields"
            if data.get("layer") not in {"core", "services", "adapters", "ui"}:
                return False, "schema:invalid_layer"
            if not all(isinstance(data.get(k), str) for k in ("module", "code")):
                return False, "schema:invalid_types"
            # Architecture rules check
            code = data.get("code", "")
            layer = data.get("layer", "")
            if layer == "core" and re.search(r"\bimport\b.*(service|adapter|ui)", code, re.IGNORECASE):
                return False, "arch:core_has_deps"
            if layer == "ui" and re.search(r"\b(db|database|sql|query|model)\b", code, re.IGNORECASE):
                return False, "arch:ui_has_logic"
        except (json.JSONDecodeError, AttributeError):
            return False, "schema:invalid_json"

    out_tokens = max(1, len(text) // 4)
    if out_tokens > max_tokens * 1.5:
        return False, f"budget:overflow({out_tokens}>{max_tokens})"

    return True, "ok"


def enforce_budget(text: str, max_tokens: int) -> str:
    """BUDGET_ENFORCE — compress → retry handled in pipeline.
    Final trim if still over limit."""
    out_tokens = max(1, len(text) // 4)
    if out_tokens <= max_tokens:
        return text
    char_limit = max_tokens * 4
    trimmed = text[:char_limit]
    # Cut at last newline to avoid mid-line truncation
    last_nl = trimmed.rfind("\n")
    if last_nl > char_limit * 0.8:
        trimmed = trimmed[:last_nl]
    return trimmed + "\n[BUDGET:truncated]"


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(
    client: anthropic.Anthropic,
    raw_input: str,
    state: SessionState | None = None,
    verbose: bool = True,
) -> str:
    """Run the full Cloud pipeline."""

    # ── CONTEXT_SELECT ─────────────────────────────────────────────────────────
    cmd = parse_command(raw_input)
    task = cmd["task"]
    max_tokens = cmd["max_tokens"]
    constraints = cmd["constraints"]

    # Cache check (session mode only)
    if state is not None:
        cached = state.cache_get(task, constraints)
        if cached is not None:
            if verbose:
                print("CACHE: hit — reusing previous output")
            return cached

    # Delta processing (session mode, not cold start)
    if state is not None and not state.cold and state.prev_input is not None:
        delta = state.input_delta(task)
        if not delta:
            if verbose:
                print("DELTA: no change — returning previous output")
            return state.prev_output or ""
        task = delta  # process only the new portion

    # ── STRIP (local pass) ─────────────────────────────────────────────────────
    # Skip on cold start (no prior context to strip against)
    cold = state.cold if state else True
    if not cold:
        task = strip_local(task)
    else:
        task = strip_local(task)  # still strip filler even on cold start

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
        print(f"  estimate : in=~{est['tokens_in']} out_pred=~{est['tokens_out_pred']} complexity={est['complexity_score']}")
        print(f"  mode     : {mode}")
        print(f"  model    : {model_key} → {MODELS[model_key]}")
        print(f"  budget   : {max_tokens} tokens")
        print()

    retry = 0
    current_mode = mode
    current_model = model_key
    step = 0

    while retry <= 2:
        step += 1

        # ── EXECUTE ────────────────────────────────────────────────────────────
        try:
            output = execute_mode_pipeline(
                client, task, current_mode, current_model,
                max_tokens, constraints, cls,
            )
        except anthropic.APIError as e:
            return f"ERROR: {e}"

        # ── VALIDATE ───────────────────────────────────────────────────────────
        valid, reason = validate_output(output, current_mode, max_tokens)

        # Snapshot this step
        if state is not None:
            state.snapshot(step, output, reason if valid else f"FAIL:{reason}")

        # ── BUDGET_ENFORCE ─────────────────────────────────────────────────────
        if valid:
            output = enforce_budget(output, max_tokens)
            # Update session state
            if state is not None:
                state.cache_put(task, constraints, output)
                state.prev_input = task
                state.prev_output = output
                state.cold = False
            return output

        # ── FAILURE RECOVERY ───────────────────────────────────────────────────
        retry += 1

        if retry == 1:
            # Escalate model
            idx = MODEL_ORDER.index(current_model)
            if idx < len(MODEL_ORDER) - 1:
                current_model = MODEL_ORDER[idx + 1]
                if verbose:
                    print(f"RECOVERY {retry}: model escalation → {current_model} [{reason}]")
            else:
                if verbose:
                    print(f"RECOVERY {retry}: already at LARGE [{reason}]")

        elif retry == 2:
            # Force STRIP + TOKEN (two-step degrade)
            current_mode = "STRIP"
            if verbose:
                print(f"RECOVERY {retry}: forcing STRIP+TOKEN [{reason}]")

        else:
            # Minimal fallback — return last valid snapshot if available
            if state is not None:
                snap = state.last_valid_snapshot()
                if snap:
                    if verbose:
                        print("FALLBACK: resuming from last valid snapshot")
                    return snap["output"]
            if verbose:
                print(f"FALLBACK: retries exhausted [{reason}]")
            return f"RESULT: {task[:300]}"

    return f"RESULT: {task[:300]}"


# ── Entry point ────────────────────────────────────────────────────────────────

def print_banner():
    print("CLOUD: active")
    print("PIPELINE: CONTEXT_SELECT→STRIP→ESTIMATE→CLASSIFY→MODE_SELECT→MODEL_SELECT→EXECUTE→VALIDATE→BUDGET_ENFORCE")
    print(f"MODE: AUTO  MODEL: SMALL  MAX_TOKENS: {DEFAULT_MAX_TOKENS}")
    print()


def main():
    client = anthropic.Anthropic()

    # Piped input — stateless, no session cache
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            result = run_pipeline(client, raw, state=None)
            print("\nOUTPUT:")
            print(result)
        return

    # Args mode — stateless single task
    if len(sys.argv) > 1:
        raw = " ".join(sys.argv[1:])
        print_banner()
        result = run_pipeline(client, raw, state=None)
        print("\nOUTPUT:")
        print(result)
        return

    # Interactive session — stateful with cache + delta + snapshots
    print_banner()
    print("SESSION MODE")
    print("  Enter task on one line, or use command format:")
    print("    TASK: <desc>  MODE: TOKEN|MODULE|STRIP  MODEL: SMALL|MEDIUM|LARGE  MAX_TOKENS: <n>")
    print("  Multi-line input: end with a blank line.")
    print("  Type 'exit' to quit, 'snapshots' to view history.")
    print()

    state = SessionState()

    while True:
        lines = []
        prompt = ">> "
        try:
            while True:
                try:
                    line = input(prompt)
                except EOFError:
                    if lines:
                        break
                    print("\nSESSION: terminated")
                    return

                cmd_lower = line.strip().lower()
                if cmd_lower == "exit":
                    print("SESSION: terminated")
                    return
                if cmd_lower == "snapshots":
                    if not state.snapshots:
                        print("SNAPSHOTS: none")
                    else:
                        for s in state.snapshots:
                            print(f"  step={s['step']} validation={s['validation']} output={s['output'][:60]!r}")
                    print()
                    continue

                if line.strip() == "":
                    if lines:
                        break
                    continue

                lines.append(line)
                prompt = "   "

        except KeyboardInterrupt:
            print("\nSESSION: terminated")
            return

        if not lines:
            continue

        raw = "\n".join(lines)
        result = run_pipeline(client, raw, state=state)
        print("\nOUTPUT:")
        print(result)
        print()


if __name__ == "__main__":
    main()

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
import os

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic SDK not installed. Run: pip install anthropic")
    sys.exit(1)

# ── Model tiers ────────────────────────────────────────────────────────────────

MODELS = {
    "SMALL":  "claude-haiku-4-5",
    "MEDIUM": "claude-sonnet-4-6",
    "LARGE":  "claude-opus-4-6",
}

MODEL_ORDER = ["SMALL", "MEDIUM", "LARGE"]

DEFAULT_MAX_TOKENS = 800

# ── System prompts ─────────────────────────────────────────────────────────────

_SYS_TOKEN = """\
SYSTEM: Cloud Plugin — TOKEN mode
RULES:
- ultra-compact output only
- no explanation, no prose, no preamble
- key:value format where possible
- omit stopwords
- minimize tokens aggressively"""

_SYS_MODULE = """\
SYSTEM: Cloud Plugin — MODULE mode
OUTPUT: valid JSON matching this schema exactly:
  {"module": "<string>", "layer": "<core|services|adapters|ui>", "code": "<string>"}
ARCHITECTURE:
  core: no external deps
  services: imports core only
  adapters: imports services + core
  ui: no business logic
RULES:
- output ONLY the JSON object, nothing else
- reject any response that violates schema or architecture"""

_SYS_STRIP = """\
SYSTEM: Cloud Plugin — STRIP mode
RULES:
- extract and return: task, constraints, required data
- remove: filler, repetition, conversational text, verbose phrasing
- compact: sentences → key:value, collapse lists, remove stopwords
- output compacted context only — no generation, no explanation"""

_SYS_EXECUTE = """\
SYSTEM: Cloud Plugin — EXECUTE
RULES:
- no filler, no preamble, no summaries
- structure > prose
- deterministic output
- enforce schema + architecture
- minimize tokens"""

SYSTEM_PROMPTS = {
    "TOKEN":  _SYS_TOKEN,
    "MODULE": _SYS_MODULE,
    "STRIP":  _SYS_STRIP,
}

# ── Pipeline stages ────────────────────────────────────────────────────────────

def parse_command(text: str) -> dict:
    """CONTEXT_SELECT — parse command interface format."""
    result = {
        "task": None,
        "mode": "AUTO",
        "model": "AUTO",
        "max_tokens": DEFAULT_MAX_TOKENS,
        "constraints": None,
        "raw": text.strip(),
    }
    lines = text.strip().splitlines()
    found_key = False
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("TASK:"):
            result["task"] = stripped[5:].strip()
            found_key = True
        elif upper.startswith("MODE:"):
            val = stripped[5:].strip().upper()
            if val in ("AUTO", "TOKEN", "MODULE", "STRIP"):
                result["mode"] = val
            found_key = True
        elif upper.startswith("MODEL:"):
            val = stripped[6:].strip().upper()
            if val in ("AUTO", "SMALL", "MEDIUM", "LARGE"):
                result["model"] = val
            found_key = True
        elif upper.startswith("MAX_TOKENS:"):
            try:
                result["max_tokens"] = int(stripped[11:].strip())
            except ValueError:
                pass
            found_key = True
        elif upper.startswith("CONSTRAINTS:"):
            result["constraints"] = stripped[12:].strip()
            found_key = True

    if not found_key:
        result["task"] = text.strip()

    if result["task"] is None:
        result["task"] = text.strip()

    return result


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return max(1, len(text) // 4)


def strip_local(text: str) -> str:
    """Local filler removal — fast pass before API STRIP call."""
    filler = [
        r"\bplease\b", r"\bcould you\b", r"\bwould you\b", r"\bjust\b",
        r"\bkindly\b", r"\bi want you to\b", r"\bi need you to\b",
        r"\bi think\b", r"\bmaybe\b", r"\bbasically\b", r"\bactually\b",
        r"\bfeel free to\b", r"\bif you can\b", r"\bif possible\b",
    ]
    result = text
    for pattern in filler:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+", " ", result).strip()


def classify_task(task: str) -> dict:
    """CLASSIFY — determine type, size, noise."""
    t = task.lower()

    if any(w in t for w in ["format", "indent", "style", "lint", "sort", "align"]):
        task_type = "format"
    elif any(w in t for w in ["refactor", "rewrite", "restructure", "rename", "reorganize"]):
        task_type = "refactor"
    elif any(w in t for w in ["analyze", "explain", "review", "audit", "check", "describe", "summarize"]):
        task_type = "analyze"
    else:
        task_type = "generate"

    token_count = estimate_tokens(task)
    if token_count < 50:
        size = "small"
    elif token_count < 300:
        size = "medium"
    else:
        size = "large"

    filler_count = sum(
        1 for w in ["please", "could", "would", "just", "maybe", "think", "want", "need"]
        if w in t
    )
    noise = "high" if filler_count >= 3 else "low"

    return {"type": task_type, "size": size, "noise": noise}


def select_mode(classification: dict, requested: str) -> str:
    """MODE_SELECT."""
    if requested not in ("AUTO", ""):
        return requested
    if classification["noise"] == "high":
        return "STRIP"
    if classification["type"] == "format":
        return "TOKEN"
    return "MODULE"


def select_model(mode: str, classification: dict, requested: str) -> str:
    """MODEL_SELECT — lightweight-first, escalate for complexity."""
    if requested not in ("AUTO", ""):
        return requested
    if classification["type"] == "refactor" or classification["size"] == "large":
        return "LARGE"
    if mode == "MODULE":
        return "MEDIUM"
    return "SMALL"


def api_call(
    client: anthropic.Anthropic,
    task: str,
    mode: str,
    model_key: str,
    max_tokens: int,
    constraints: str | None,
) -> str:
    """EXECUTE — call Claude API."""
    model_id = MODELS[model_key]
    system = SYSTEM_PROMPTS.get(mode, _SYS_EXECUTE)

    user_parts = [f"TASK: {task}"]
    if constraints:
        user_parts.append(f"CONSTRAINTS: {constraints}")
    user_content = "\n".join(user_parts)

    response = client.messages.create(
        model=model_id,
        max_tokens=max(max_tokens, 256),
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")


def validate_output(text: str, mode: str, max_tokens: int) -> tuple[bool, str]:
    """VALIDATE — check mode compliance, schema, budget."""
    if mode == "MODULE":
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return False, "schema:missing_json"
            data = json.loads(match.group())
            required = {"module", "layer", "code"}
            valid_layers = {"core", "services", "adapters", "ui"}
            if not required.issubset(data.keys()):
                return False, "schema:missing_fields"
            if data.get("layer") not in valid_layers:
                return False, "schema:invalid_layer"
            if not all(isinstance(data.get(k), str) for k in ("module", "code")):
                return False, "schema:invalid_types"
        except (json.JSONDecodeError, AttributeError):
            return False, "schema:invalid_json"

    out_tokens = estimate_tokens(text)
    if out_tokens > max_tokens * 1.5:
        return False, f"budget:overflow({out_tokens}>{max_tokens})"

    return True, "ok"


def enforce_budget(text: str, max_tokens: int) -> str:
    """BUDGET_ENFORCE — trim if over limit."""
    if estimate_tokens(text) <= max_tokens:
        return text
    char_limit = max_tokens * 4
    trimmed = text[:char_limit]
    last_newline = trimmed.rfind("\n")
    if last_newline > char_limit * 0.8:
        trimmed = trimmed[:last_newline]
    return trimmed + "\n[TRUNCATED:budget]"


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(client: anthropic.Anthropic, raw_input: str, verbose: bool = True) -> str:
    """Run the full Cloud pipeline on raw input."""

    # CONTEXT_SELECT
    cmd = parse_command(raw_input)
    task = cmd["task"]
    max_tokens = cmd["max_tokens"]
    constraints = cmd["constraints"]

    # STRIP (local pass)
    stripped = strip_local(task)

    # ESTIMATE
    tokens_in = estimate_tokens(stripped)
    tokens_out_pred = min(tokens_in * 3, max_tokens * 2)

    # CLASSIFY
    cls = classify_task(stripped)

    # MODE_SELECT
    mode = select_mode(cls, cmd["mode"])
    if tokens_out_pred > max_tokens and mode != "STRIP":
        mode = "TOKEN"  # force compact if predicted overflow

    # MODEL_SELECT
    model_key = select_model(mode, cls, cmd["model"])
    if model_key not in MODELS:
        model_key = "MEDIUM"

    if verbose:
        print("PIPELINE:")
        print(f"  classify: type={cls['type']} size={cls['size']} noise={cls['noise']}")
        print(f"  mode: {mode}")
        print(f"  model: {model_key} → {MODELS[model_key]}")
        print(f"  budget: ~{tokens_in}in / {max_tokens}out")
        print()

    retry = 0
    current_mode = mode
    current_model = model_key
    last_error = ""

    while retry <= 2:
        # STRIP via API if mode is STRIP (preprocess, then re-execute in TOKEN)
        if current_mode == "STRIP":
            try:
                stripped_ctx = api_call(client, stripped, "STRIP", current_model, max_tokens, constraints)
                # After strip, run TOKEN on the compacted context
                output = api_call(client, stripped_ctx, "TOKEN", current_model, max_tokens, None)
            except anthropic.APIError as e:
                return f"ERROR: {e}"
        else:
            # EXECUTE
            try:
                output = api_call(client, stripped, current_mode, current_model, max_tokens, constraints)
            except anthropic.APIError as e:
                return f"ERROR: {e}"

        # VALIDATE
        valid, reason = validate_output(output, current_mode, max_tokens)

        # BUDGET_ENFORCE
        output = enforce_budget(output, max_tokens)

        if valid:
            return output

        # FAILURE RECOVERY
        last_error = reason
        retry += 1

        if retry == 1:
            # Escalate model
            idx = MODEL_ORDER.index(current_model)
            if idx < len(MODEL_ORDER) - 1:
                current_model = MODEL_ORDER[idx + 1]
                if verbose:
                    print(f"RETRY {retry}: model escalation → {current_model} (reason: {last_error})")
            else:
                if verbose:
                    print(f"RETRY {retry}: already at max model (reason: {last_error})")
        elif retry == 2:
            # Degrade mode
            current_mode = "TOKEN"
            if verbose:
                print(f"RETRY {retry}: mode degraded → TOKEN (reason: {last_error})")

    # Minimal fallback
    if verbose:
        print(f"FALLBACK: all retries exhausted (last_error: {last_error})")
    return f"RESULT: {stripped[:300]}"


# ── Entry point ────────────────────────────────────────────────────────────────

def print_banner():
    print("CLOUD: active")
    print("PIPELINE: CONTEXT_SELECT→STRIP→ESTIMATE→CLASSIFY→MODE_SELECT→MODEL_SELECT→EXECUTE→VALIDATE→BUDGET_ENFORCE")
    print(f"MODE: AUTO  MODEL: MEDIUM  MAX_TOKENS: {DEFAULT_MAX_TOKENS}")
    print()


def main():
    client = anthropic.Anthropic()

    # Piped input
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            result = run_pipeline(client, raw)
            print("OUTPUT:")
            print(result)
        return

    # Args mode (single task)
    if len(sys.argv) > 1:
        raw = " ".join(sys.argv[1:])
        print_banner()
        result = run_pipeline(client, raw)
        print("OUTPUT:")
        print(result)
        return

    # Interactive session
    print_banner()
    print("SESSION MODE")
    print("  Enter task on one line, or use command format:")
    print("    TASK: <desc>  MODE: TOKEN|MODULE|STRIP  MODEL: SMALL|MEDIUM|LARGE  MAX_TOKENS: <n>")
    print("  Multi-line: end with blank line. Type 'exit' to quit.")
    print()

    while True:
        lines = []
        try:
            prompt = ">> "
            while True:
                try:
                    line = input(prompt)
                except EOFError:
                    if lines:
                        break
                    print("\nSESSION: terminated")
                    return

                if line.strip().lower() == "exit":
                    print("SESSION: terminated")
                    return

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
        result = run_pipeline(client, raw)
        print()
        print("OUTPUT:")
        print(result)
        print()


if __name__ == "__main__":
    main()

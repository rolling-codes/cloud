"""Cloud CLI entry point.

Three invocation modes:
  Interactive  python -m cloud                  stateful session
  Args         python -m cloud "TASK: ..."      single task, stateless
  Piped        echo "TASK: ..." | python -m cloud  single task, stateless
"""

import sys

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic SDK not installed. Run: pip install anthropic")
    sys.exit(1)

from .pipeline import run_pipeline
from .session import SessionState
from .parser import DEFAULT_MAX_TOKENS
from .selector import MODELS


_BANNER = (
    "CLOUD: active\n"
    "PIPELINE: CONTEXT_SELECT→STRIP→ESTIMATE→CLASSIFY→"
    "MODE_SELECT→MODEL_SELECT→EXECUTE→VALIDATE→BUDGET_ENFORCE\n"
    f"MODE: AUTO  MODEL: SMALL  MAX_TOKENS: {DEFAULT_MAX_TOKENS}"
)


def _run_and_print(client: anthropic.Anthropic, raw: str, state: SessionState | None) -> None:
    """Run the pipeline and print the result."""
    result = run_pipeline(client, raw, state=state)
    print("\nOUTPUT:")
    print(result)


def main() -> None:
    client = anthropic.Anthropic()

    # ── Piped input — stateless ────────────────────────────────────────────────
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            result = run_pipeline(client, raw, state=None)
            print("\nOUTPUT:")
            print(result)
        return

    # ── Args mode — stateless single task ─────────────────────────────────────
    if len(sys.argv) > 1:
        raw = " ".join(sys.argv[1:])
        print(_BANNER)
        print()
        _run_and_print(client, raw, state=None)
        return

    # ── Interactive session — stateful ─────────────────────────────────────────
    print(_BANNER)
    print()
    print("SESSION MODE")
    print("  Enter task on one line, or use command format:")
    print(
        "    TASK: <desc>  MODE: TOKEN|MODULE|STRIP"
        "  MODEL: SMALL|MEDIUM|LARGE  MAX_TOKENS: <n>"
    )
    print("  Multi-line input: end with a blank line.")
    print("  Commands: 'exit' to quit, 'snapshots' to view step history.")
    print()

    state = SessionState()

    while True:
        lines: list[str] = []
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

                cmd = line.strip().lower()

                if cmd == "exit":
                    print("SESSION: terminated")
                    return

                if cmd == "snapshots":
                    if not state.snapshots:
                        print("SNAPSHOTS: none")
                    else:
                        for s in state.snapshots:
                            preview = repr(s["output"][:60])
                            print(
                                f"  step={s['step']} "
                                f"validation={s['validation']} "
                                f"output={preview}"
                            )
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

        _run_and_print(client, "\n".join(lines), state=state)
        print()

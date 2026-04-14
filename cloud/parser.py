"""CONTEXT_SELECT — parse the Cloud command interface.

Extracts: task, constraints, required_prior_outputs.
Ignores:  all other history.
"""

DEFAULT_MAX_TOKENS = 800


def parse_command(text: str) -> dict:
    """Parse Cloud command interface format.

    Recognized keys (any order, case-insensitive):
      TASK:        task description (required)
      MODE:        AUTO | TOKEN | MODULE | STRIP
      MODEL:       AUTO | SMALL | MEDIUM | LARGE
      MAX_TOKENS:  integer budget
      CONSTRAINTS: free-text constraints

    If no recognized keys are found the entire input is treated as TASK.
    """
    result: dict = {
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

    # No recognized keys → treat entire input as task
    if not found_key or result["task"] is None:
        result["task"] = text.strip()

    return result

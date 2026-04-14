"""STRIP — context stripping engine.

Always on. Reruns before output if over budget.

Remove: filler, repetition, conversational text, verbose phrasing.
Keep:   task, constraints, required data.
Compact: sentences → key:value, collapse lists, remove stop words.
"""

import re

# Fixed filler patterns — stable ordering enforced for determinism.
_FILLER: tuple[str, ...] = (
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
    r"\bgo ahead and\b",
    r"\bcan you\b",
)


def strip_local(text: str) -> str:
    """Fast local strip pass — no API call required.

    Applied on every input. Rerun on output if over budget.
    """
    result = text
    for pattern in _FILLER:  # stable ordering
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    # Collapse whitespace
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()

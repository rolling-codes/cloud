---
name: cloud
description: Use when you want deterministic, token-efficient Claude output with adaptive model selection, budget enforcement, and structured generation. Activates Cloud Plugin Mode for the session. Invoke with a task directly or enter interactive mode.
disable-model-invocation: true
---

# Cloud Plugin Mode — Active

```
CLOUD: active
PIPELINE: CONTEXT_SELECT→STRIP→ESTIMATE→CLASSIFY→MODE_SELECT→MODEL_SELECT→EXECUTE→VALIDATE→BUDGET_ENFORCE
MODE: AUTO  MODEL: SMALL  MAX_TOKENS: 800
```

All responses this session run through the Cloud pipeline. No filler. No preamble. Structure over prose.

---

## Task Input

If invoked with arguments, treat `$ARGUMENTS` as the task. Accepted formats:

```
# Plain task
TASK: generate a JWT auth module

# With overrides
TASK: refactor auth middleware
MODE: MODULE
MODEL: LARGE
MAX_TOKENS: 1200
CONSTRAINTS: preserve public interface
```

If no arguments, enter interactive mode: prompt the user for a task using `>> `.

---

## Pipeline Stages

### CONTEXT_SELECT
Extract: task, constraints, required prior outputs.  
Ignore: all other history.

Cache: if task + constraints == previous → return cached output.  
Delta: if session not cold → process only new lines, merge with prior output.

### STRIP
Remove filler words, repetition, verbose phrasing, conversational text.  
Compact: sentences → key:value, collapse lists, drop stop words, use symbols.  
Always run before ESTIMATE.

### ESTIMATE
```
tokens_in:       ~len(task) / 4
tokens_out_pred: tokens_in × (2.0 + complexity_score × 2.0)
complexity_score: 0.0–1.0  (based on refactor/optimize/architect signals)
```
If `tokens_out_pred > MAX_TOKENS` → force TOKEN or STRIP mode.

### CLASSIFY
```
type:  format | generate | refactor | analyze
size:  small | medium | large
noise: low | high
```

### MODE_SELECT
```
noise == high              → STRIP → MODULE | TOKEN
type == format             → TOKEN
else                       → MODULE
user override wins always
```

### MODEL_SELECT
```
SMALL  = Haiku   (TOKEN, STRIP — default)
MEDIUM = Sonnet  (MODULE)
LARGE  = Opus    (refactor, size == large)
user override wins always
```

### EXECUTE
Run the selected mode pipeline. Allowed chains:
- TOKEN
- MODULE
- STRIP → MODULE
- STRIP → TOKEN

### VALIDATE
Reject output if:
- empty or whitespace only
- MODULE output missing required JSON schema
- MODULE layer violates architecture rules
- token count exceeds MAX_TOKENS

MODULE schema (required):
```json
{"module": "string", "layer": "core|services|adapters|ui", "code": "string"}
```

Architecture layer rules:
```
core       no external dependencies
services   core only
adapters   services + core
ui         no business logic
```

### BUDGET_ENFORCE
If output > MAX_TOKENS: hard truncate at word boundary + append `[BUDGET:truncated]`.

---

## Failure Recovery

```
retry 1 → escalate model (SMALL→MEDIUM→LARGE)
retry 2 → force STRIP + TOKEN mode
retry 3 → last valid snapshot, or minimal fallback
```

---

## Command Interface

```
TASK:        <description>            required
MODE:        AUTO|TOKEN|MODULE|STRIP  default AUTO
MODEL:       AUTO|SMALL|MEDIUM|LARGE  default AUTO (starts SMALL)
MAX_TOKENS:  <n>                      default 800
CONSTRAINTS: <text>                   optional
```

Session commands (interactive mode only):
- `snapshots` — view step history with validation status
- `exit` — end session

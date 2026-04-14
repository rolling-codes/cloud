# Cloud Plugin

Deterministic execution control layer for Claude. Every response runs through a structured pipeline that strips noise, estimates complexity, selects the right model and mode, validates output, and enforces token budgets — automatically.

---

## Why Cloud?

Default Claude is optimized for helpful conversation. Cloud is optimized for production output.

The difference:

| Default Claude | Cloud |
| -------------- | ----- |
| Verbose by default | Compact by default |
| Uses whatever model | Starts SMALL, escalates only as needed |
| No output schema | MODULE mode enforces JSON schema |
| No budget | Hard token cap with truncation marker |
| Single attempt | Retry with model escalation + mode degradation |
| Full context every time | Cache + delta: reuse unchanged work |

Cloud is the right tool when you need the output to be correct, compact, and consistent — not when you want a conversation.

---

## Install

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
```

---

## Usage

### Python CLI

```bash
# Interactive session (stateful: cache, delta, snapshots)
python -m cloud

# Single task via args
python -m cloud "TASK: generate a JWT auth module"

# Piped input
echo "TASK: refactor auth
MODE: MODULE
MODEL: LARGE
MAX_TOKENS: 1200" | python -m cloud
```

### Claude Code Skill

After installing this plugin, activate Cloud mode in any session:

```text
/cloud
```

Or with a task directly:

```text
/cloud TASK: generate a JWT auth module
```

---

## Pipeline

```text
INPUT → CONTEXT_SELECT → STRIP → ESTIMATE → CLASSIFY →
        MODE_SELECT → MODEL_SELECT → EXECUTE → VALIDATE →
        BUDGET_ENFORCE → OUTPUT
```

Each stage has one job and passes only what the next stage needs.

### CONTEXT_SELECT

Extracts task, constraints, and required prior outputs. In session mode: checks the cache first, computes the delta against the previous input.

### STRIP

Removes filler, repetition, and verbose phrasing before the model ever sees the input. Compacts sentences to key:value, collapses lists, drops stop words.

**Before STRIP:**

```text
I was wondering if you could maybe help me refactor the authentication
middleware to be a bit cleaner. It currently handles both JWT validation
and session management which seems like too much for one file.
```

**After STRIP:**

```text
refactor auth middleware: split JWT validation + session management into separate modules
```

### ESTIMATE

Predicts output token count based on input size and a complexity score derived from keywords (refactor, optimize, architect, etc.). If the prediction exceeds the budget, forces TOKEN or STRIP mode before any API call is made.

### CLASSIFY

Classifies the task along three axes:

- `type`: format | generate | refactor | analyze
- `size`: small | medium | large
- `noise`: low | high

Noise classification drives mode selection — high-noise input routes through STRIP first.

### MODE_SELECT + MODEL_SELECT

Picks the output mode and model tier based on classification, then applies user overrides.

### EXECUTE

Runs the mode pipeline. Multi-step modes (STRIP→MODULE, STRIP→TOKEN) make two API calls: one to reduce context, one to generate output from the reduced form.

### VALIDATE

Checks that the output is non-empty, conforms to the schema (MODULE mode), respects architecture layer rules, and fits within budget. Rejects and triggers recovery if any check fails.

### BUDGET_ENFORCE

Hard-trims output at the token boundary and appends `[BUDGET:truncated]` if the model exceeded the cap.

---

## Modes

| Mode | Output | Default model | When |
| ---- | ------ | ------------- | ---- |
| TOKEN | Ultra-compact, key:value | SMALL (Haiku) | Formatting, labeling, tagging |
| MODULE | JSON schema with layer | MEDIUM (Sonnet) | Code generation, structured output |
| STRIP | Context reduction → chains to MODULE or TOKEN | SMALL | Noisy or verbose input |

### TOKEN example

**Input:**

```text
TASK: list the HTTP methods for a REST CRUD API
MODE: TOKEN
```

**Output:**

```text
create:POST list:GET get:GET/{id} update:PUT/{id} patch:PATCH/{id} delete:DELETE/{id}
```

### MODULE example

**Input:**

```text
TASK: generate a user repository interface
MODE: MODULE
```

**Output:**

```json
{
  "module": "UserRepository",
  "layer": "core",
  "code": "interface UserRepository {\n  findById(id: string): Promise<User | null>;\n  findByEmail(email: string): Promise<User | null>;\n  save(user: User): Promise<User>;\n  delete(id: string): Promise<void>;\n}"
}
```

The `layer` field enforces architecture rules — `core` means no external dependencies allowed.

---

## Model Tiers

| Tier | Model | Used for |
| ---- | ----- | -------- |
| SMALL | Haiku | TOKEN mode, STRIP pass, default start |
| MEDIUM | Sonnet | MODULE mode |
| LARGE | Opus | Refactor tasks, `size == large` |

Cloud starts every task at SMALL. If the output fails validation, it escalates to MEDIUM, then LARGE. You pay for the larger model only when the smaller one couldn't do the job.

Override at any time:

```text
MODEL: LARGE
```

---

## Command Format

```text
TASK:        <description>            required
MODE:        AUTO|TOKEN|MODULE|STRIP  default AUTO
MODEL:       AUTO|SMALL|MEDIUM|LARGE  default AUTO
MAX_TOKENS:  <n>                      default 800
CONSTRAINTS: <text>                   optional
```

**Examples:**

```text
TASK: generate an in-memory cache service
MODE: MODULE
MAX_TOKENS: 600
CONSTRAINTS: no external dependencies, TTL per key
```

```text
TASK: classify these log entries as error, warn, or info
MODE: TOKEN
MAX_TOKENS: 200
```

```text
TASK: refactor the payment processor to separate charge logic from receipt generation
MODEL: LARGE
MAX_TOKENS: 2000
CONSTRAINTS: preserve the public PaymentProcessor interface
```

---

## Session Mode

Interactive sessions are stateful. Three features activate automatically:

### Cache

If you submit the same task (same text, same constraints) a second time, Cloud returns the cached output instantly — no API call.

### Delta

If your new input overlaps significantly with the previous input, Cloud processes only the new lines and merges with the prior output. Useful when iterating on a task.

### Snapshots

Every pipeline execution is recorded. In the interactive session:

```text
>> snapshots
  step=1 validation=ok output='{"module": "UserRepository", "layer": "core"...'
  step=2 validation=FAIL:empty output=''
  step=3 validation=ok output='{"module": "UserRepository", "layer": "core"...'
```

On total failure, Cloud falls back to the last valid snapshot automatically.

---

## Failure Recovery

Cloud retries failed outputs before giving up:

```text
retry 1 → escalate model (SMALL → MEDIUM → LARGE)
retry 2 → force STRIP + TOKEN (two-step degraded mode)
retry 3 → last valid snapshot, or minimal fallback
```

You see recovery diagnostics in the output:

```text
RECOVERY 1: model escalation → MEDIUM [empty output]
RECOVERY 2: forcing STRIP+TOKEN [schema invalid]
FALLBACK: last valid snapshot
```

---

## When to Use Cloud

Good fit:

- Code generation where schema and layer correctness matter
- Batch tasks where you want consistent, compact output
- Token-constrained environments (narrow context windows, rate limits)
- Iterative sessions where the same task evolves across turns
- Any task where you want model cost to scale with task difficulty

Not a good fit:

- Open-ended conversation or brainstorming
- Tasks requiring Claude's full context awareness across many prior turns
- Creative writing where verbose, expressive output is the goal
- One-off questions where pipeline overhead isn't worth it

---

## Troubleshooting

**`ERROR: anthropic SDK not installed`**

```bash
pip install anthropic
```

**Output always hits `[BUDGET:truncated]`**

Increase `MAX_TOKENS` or switch to `MODE: TOKEN` to get a more compressed output within the same budget.

**MODULE output fails validation**

The model returned something that wasn't valid JSON matching the required schema. Cloud will retry with model escalation automatically. If it keeps failing, add `CONSTRAINTS:` to tighten the instruction.

**`DELTA: no change` and no output**

Your new input is identical to the previous one. Cloud detected no change and returned the cached output. If you meant to run a fresh task, change the wording or add a new constraint.

**Recovery fires every time**

The task is likely too large for the budget. Try:

```text
MAX_TOKENS: 2000
MODEL: LARGE
```

Or split the task into smaller sub-tasks.

---

## Plugin Structure

```text
cloud-plugin/
├── .claude-plugin/plugin.json   # Plugin metadata
├── Cloud.md                     # Spec
├── README.md
├── skills/cloud/SKILL.md        # Claude Code session skill
└── cloud/                       # Python package
    ├── __init__.py
    ├── __main__.py              # python -m cloud entry
    ├── cli.py                   # CLI / interactive session
    ├── pipeline.py              # Orchestrator
    ├── parser.py                # CONTEXT_SELECT
    ├── stripper.py              # STRIP engine
    ├── estimator.py             # ESTIMATE
    ├── classifier.py            # CLASSIFY
    ├── selector.py              # MODE_SELECT + MODEL_SELECT
    ├── executor.py              # EXECUTE + mode pipelines
    ├── validator.py             # VALIDATE + BUDGET_ENFORCE
    └── session.py               # Cache, delta, snapshots
```

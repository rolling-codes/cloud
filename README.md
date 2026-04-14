# Cloud Plugin

Deterministic execution control layer for Claude.

## Pipeline

```
INPUT → CONTEXT_SELECT → STRIP → ESTIMATE → CLASSIFY →
        MODE_SELECT → MODEL_SELECT → EXECUTE → VALIDATE →
        BUDGET_ENFORCE → OUTPUT
```

## Install

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
```

## Usage

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

## Command Format

```
TASK:        <description>          required
MODE:        AUTO|TOKEN|MODULE|STRIP  default AUTO
MODEL:       AUTO|SMALL|MEDIUM|LARGE  default AUTO (starts SMALL)
MAX_TOKENS:  <n>                    default 800
CONSTRAINTS: <text>                 optional
```

## Modes

| Mode   | Output           | Default model |
|--------|------------------|---------------|
| TOKEN  | ultra-compact    | SMALL (Haiku) |
| MODULE | JSON schema      | MEDIUM (Sonnet) |
| STRIP  | context reduction → chains to MODULE or TOKEN | SMALL |

## Model Tiers

| Tier   | Model   | Used for |
|--------|---------|----------|
| SMALL  | Haiku   | TOKEN, STRIP (default start) |
| MEDIUM | Sonnet  | MODULE |
| LARGE  | Opus    | refactor, large tasks |

## Plugin Structure

```
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

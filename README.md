# ML Metaoptimization Skill Repo

This repository defines the `ml-metaoptimization` skill plus its supporting reference contracts and example campaign.
This repository is a contract-only scope for the `ml-metaoptimization` skill.
It pins the public docs, examples, and fixtures for the orchestration runtime.
It does not simulate a live Copilot host or remote queue backend.

## Validation

Install the validation dependency from the repo root:

```bash
python3 -m pip install --user -r requirements.txt
```

Run the validation suite from the repo root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

The tests pin the public contract for:
- the example campaign file
- backend stdout JSON payload shapes
- state-file fixtures and lifecycle pairing rules
- cross-document consistency between the skill and reference docs

## Ecosystem

This orchestrator delegates to the following worker targets:

- `metaopt-ideation-worker` — Step-3 ideation custom agent wrapper
- `metaopt-selection-worker` — Step-5 selection custom agent wrapper
- `metaopt-design-worker` — Step-6 design custom agent wrapper
- `metaopt-materialization-worker` — Step-7 materialization custom agent wrapper
- `metaopt-diagnosis-worker` — shared diagnosis custom agent wrapper
- `metaopt-analysis-worker` — remote-results analysis custom agent wrapper
- `metaopt-rollover-worker` — iteration rollover custom agent wrapper

Backend execution:
- [`hetzner-delegation`](https://github.com/jc1122/hetzner-delegation) — remote compute delegation skill
- [`ray-hetzner`](https://github.com/jc1122/ray-hetzner) — Ray cluster and queue runtime

Maintenance:
- [`repo-audit-refactor-optimize`](https://github.com/jc1122/repo-audit-refactor-optimize) — background maintenance lane

## Runtime Notes

`SKILL.md` describes the intended GitHub Copilot-style orchestration runtime.

`agents/openai.yaml` is separate catalog metadata for OpenAI/Codex-style runtimes. It exists so those runtimes can surface this skill in their own UI/catalog flows; it does not define Copilot dispatch behavior.

## Delegated Steps

This repository includes dedicated custom agent profiles and control agents for all delegated phases.
The authoritative reference for control-agent scopes, handoff envelopes, and state-patch ownership is
`references/control-protocol.md`. Per-state dispatch details (worker targets, inputs, outputs) are in
`references/dispatch-guide.md`. The state machine with the control-agent dispatch map is in
`references/state-machine.md`.

### Step 1 — `metaopt-load-campaign`

Agent profile: `.github/agents/metaopt-load-campaign.agent.md`
Handoff script: `scripts/load_campaign_handoff.py`
Scope: `LOAD_CAMPAIGN` — validates campaign YAML, computes identity/runtime hashes, performs advisory state peek.

### Step 2 — `metaopt-hydrate-state`

Agent profile: `.github/agents/metaopt-hydrate-state.agent.md`
Handoff script: `scripts/hydrate_state_handoff.py`
Scope: `HYDRATE_STATE` — resumes or initializes state, manages `AGENTS.md` hook, verifies worker-target availability.

### Steps 3/4 — `metaopt-background-control`

Agent profile: `.github/agents/metaopt-background-control.agent.md`
Handoff script: `scripts/background_control_handoff.py`
Scope: `MAINTAIN_BACKGROUND_POOL`, `WAIT_FOR_PROPOSAL_THRESHOLD` — owns lane assignment, staged task-file generation, proposal-pool updates, and threshold readiness.

Leaf workers: `metaopt-ideation-worker`, `repo-audit-refactor-optimize`.

### Steps 5/6 — `metaopt-select-design`

Agent profile: `.github/agents/metaopt-select-design.agent.md`
Handoff script: `scripts/select_and_design_handoff.py`
Scope: `SELECT_EXPERIMENT`, `DESIGN_EXPERIMENT` — freezes proposal pool, orchestrates selection and design workers, persists winning proposal and experiment design.

Leaf workers: `metaopt-selection-worker`, `metaopt-design-worker`.

### Steps 7/8 — `metaopt-local-execution-control`

Agent profile: `.github/agents/metaopt-local-execution-control.agent.md`
Handoff script: `scripts/local_execution_control_handoff.py`
Scope: `MATERIALIZE_CHANGESET`, `LOCAL_SANITY` — plans materialization, interprets sanity results, routes diagnosis retries.

Leaf workers: `metaopt-materialization-worker`, `metaopt-diagnosis-worker`.

### Steps 9/11 — `metaopt-remote-execution-control`

Agent profile: `.github/agents/metaopt-remote-execution-control.agent.md`
Handoff script: `scripts/remote_execution_control_handoff.py`
Scope: `ENQUEUE_REMOTE_BATCH`, `WAIT_FOR_REMOTE_BATCH`, `ANALYZE_RESULTS` — validates enqueue readiness, routes remote failures, interprets analysis output.

Leaf workers: `metaopt-diagnosis-worker`, `metaopt-analysis-worker`.

### Steps 12/13 — `metaopt-iteration-close-control`

Agent profile: `.github/agents/metaopt-iteration-close-control.agent.md`
Handoff script: `scripts/iteration_close_control_handoff.py`
Scope: `ROLL_ITERATION`, `QUIESCE_SLOTS` — orchestrates rollover filtering, evaluates stop conditions, routes continue-vs-complete.

Leaf workers: `metaopt-rollover-worker`.

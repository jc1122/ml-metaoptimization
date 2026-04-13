# Worker Lanes

This document describes the **leaf worker lanes** — the individual worker targets that perform concrete work. Each lane specifies the worker's inputs, outputs, dispatch contract, and lane drift rules.

For the control-agent handoff protocol, see `references/control-protocol.md`. For per-state dispatch details, see `references/dispatch-guide.md`.

## Model Classes

Model resolution is deterministic:
- `strong_reasoner`: selection, analysis, result interpretation. Resolution: `claude-opus-4.6` (or any opus >= 4.6), then `gpt-5.4` (or any gpt >= 5.4)
- `general_worker`: ideation, execution. Resolution: `claude-sonnet-4`, then `gpt-5.4` (or any gpt >= 5.4)

No `strong_coder` class — v4 does not produce code patches.

## Ideation Lane

**Worker target:** `metaopt-ideation-worker` (custom agent)

**Slot class:** `background`

**Mode:** `ideation`

**Model class:** `general_worker`

**Inputs:**
- Campaign objective (`metric`, `direction`, `improvement_threshold`)
- Prior `key_learnings`
- `baseline` (if established)
- Current and next proposal pool context (to avoid duplication)
- `proposal_policy` (current_target and related settings)

**Output:** A JSON file written to `.ml-metaopt/worker-results/<worker-id>.json`:

```json
{
  "slot_id": "bg-1",
  "mode": "ideation",
  "status": "completed",
  "summary": "Exploring residual connections based on prior learning that deeper networks overfit.",
  "proposal_candidates": [
    {
      "rationale": "Prior learnings show deeper networks overfit. This proposal explores residual connections and constrained lr to mitigate.",
      "sweep_config": {
        "method": "bayes",
        "metric": { "name": "val/accuracy", "goal": "maximize" },
        "parameters": {
          "lr": { "distribution": "log_uniform_values", "min": 1e-4, "max": 1e-2 },
          "use_residual": { "values": [true, false] },
          "num_layers": { "values": [2, 3, 4] }
        }
      }
    }
  ]
}
```

**Constraints:**
- `sweep_config` must be a valid WandB sweep config
- `sweep_config.metric.name` must match `objective_snapshot.metric`
- `sweep_config.metric.goal` must match `objective_snapshot.direction`
- Parameters must be within declared domains — no invented metrics or nonexistent config keys
- Each proposal must be meaningfully different from existing proposals in the pool

**Lane drift rules — MUST NOT:**
- Produce code patches, file diffs, or architecture change instructions
- Emit commands to run or install anything
- Suggest changes to the training script code
- Reference files outside the campaign configuration

## Analysis Lane

**Worker target:** `metaopt-analysis-worker` (custom agent)

**Slot class:** `auxiliary`

**Mode:** `analysis`

**Model class:** `strong_reasoner`

**Inputs:**
- WandB best run result: metric value, hyperparameters, run URL, run ID
- Current `baseline` (if established)
- Prior `key_learnings`
- `objective_snapshot` (for direction-aware comparison)

**Output:** A JSON file written to `.ml-metaopt/worker-results/sweep-analysis-iter-<N>.json`:

```json
{
  "improved": true,
  "best_metric_value": 0.945,
  "best_run_id": "run-abc",
  "best_run_url": "https://wandb.ai/entity/project/runs/run-abc",
  "learnings": [
    "Bayesian search over learning rate found optimal at 3e-3",
    "Residual connections consistently improve accuracy for 3+ layer models"
  ]
}
```

When no improvement: `"improved": false`, `"best_metric_value": <value>`, `"best_run_id": "<id>"`, `"best_run_url": "<url>"`.

**Constraints:**
- Baseline update comparison must be direction-aware (`objective_direction`)
- Learnings must be concrete and actionable, not generic

**Lane drift rules — MUST NOT:**
- Emit sweep configs or parameter suggestions (that is the ideation lane's job)
- Produce code changes or patches
- Make recommendations about what to try next (only report what happened)

## Selection Lane

**Worker target:** `metaopt-selection-worker` (custom agent)

**Slot class:** `selection`

**Model class:** `strong_reasoner`

**Inputs (from task file `.ml-metaopt/tasks/select-design-iter-<N>.md`):**
- Frozen `current_proposals` — the proposal pool as frozen by the plan phase (JSON array)
- `key_learnings` — accumulated learnings from prior iterations (JSON array of strings)
- Objective context: `metric`, `direction`, `improvement_threshold`
- `baseline` — current best known result (JSON object or null)

**Output:** A JSON file written to `.ml-metaopt/worker-results/select-design-iter-<N>.json`:

```json
{
  "winning_proposal": {
    "proposal_id": "<id matching an entry in the frozen proposal pool>"
  },
  "sweep_config": {
    "method": "bayes",
    "metric": { "name": "<metric>", "goal": "<maximize|minimize>" },
    "parameters": { ... }
  },
  "ranking_rationale": "One or more sentences explaining why this proposal was selected."
}
```

**Constraints:**
- `winning_proposal.proposal_id` MUST exactly match a `proposal_id` in the frozen proposal pool — no new IDs
- `sweep_config` MUST be a valid WandB sweep config with `method`, `metric`, and `parameters` keys
- Must select exactly one proposal — no partial selections or abstentions

**Lane drift rules — MUST NOT:**
- Generate new proposals or modify the proposal pool
- Mutate `.ml-metaopt/state.json`
- Make control-plane decisions (state transitions, iteration management)
- Launch subagents or dispatch workers
- Produce code patches, file diffs, or architecture changes

## Execution Lane

**Worker target:** `skypilot-wandb-worker` (custom agent)

**Dispatch:** Directive-dispatched only — not a slot-based worker.

**Model class:** `general_worker`

The execution lane is not managed by slot accounting. It is dispatched by the orchestrator when a control agent emits a `launch_sweep`, `poll_sweep`, or `run_smoke_test` directive. See `references/backend-contract.md` for the full operation contract.

**Lane drift rules — MUST NOT:**
- Analyze results or make semantic judgments about sweep quality
- Propose new sweep configurations
- Modify the training script or repository
- Call any API not documented in `references/backend-contract.md`

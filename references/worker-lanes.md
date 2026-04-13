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
- `completed_iterations` history
- Current and next proposal pool context (to avoid duplication)
- Rejected proposals and their reasons (if any)

**Output:** A JSON file written to `.ml-metaopt/worker-results/<worker-id>.json`:

```json
{
  "proposal_id": "prop-001",
  "rationale": "Exploring residual connections based on prior learning that deeper networks overfit",
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

**Output:** A JSON file written to `.ml-metaopt/worker-results/analysis-iter-<N>.json`:

```json
{
  "improved": true,
  "new_baseline": {
    "metric": "val/accuracy",
    "value": 0.945,
    "wandb_run_id": "run-abc",
    "wandb_run_url": "https://wandb.ai/entity/project/runs/run-abc",
    "established_at": "2026-04-13T15:00:00Z"
  },
  "learnings": [
    "Bayesian search over learning rate found optimal at 3e-3",
    "Residual connections consistently improve accuracy for 3+ layer models"
  ],
  "best_run_id": "run-abc"
}
```

When no improvement: `"improved": false`, `"new_baseline": null`.

**Constraints:**
- Baseline update must use direction-aware comparison from `references/contracts.md` Section 5
- `new_baseline` must only be non-null when `improved` is true
- Learnings must be concrete and actionable, not generic

**Lane drift rules — MUST NOT:**
- Emit sweep configs or parameter suggestions (that is the ideation lane's job)
- Produce code changes or patches
- Make recommendations about what to try next (only report what happened)

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

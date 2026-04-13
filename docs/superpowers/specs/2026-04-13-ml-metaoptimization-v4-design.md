# ml-metaoptimization v4 Design

**Date:** 2026-04-13
**Status:** Approved

## Problem Statement

The current ml-metaoptimization skill (v3) was built around a custom Hetzner/Ray cluster backend with bespoke Python queue scripts, slot accounting, executor event files, and patch materialization. Most of this complexity compensates for limitations of that backend — not inherent complexity of the problem.

The goal is a complete redesign: keep the skill's intelligence layer (agents that propose, select, analyze) and replace everything else with WandB Sweeps, SkyPilot, and Vast.ai.

## Scope

This skill covers **ML training campaigns exclusively**. It does not handle code changes, patches, or algorithm optimization — those belong to a separate `code-optimization` skill (future, built on `repo-audit-refactor-optimize`).

The two-phase workflow for a full project:
1. `code-optimization` — iteratively improve the codebase (separate skill, uncoupled)
2. `ml-metaoptimization` — sweep hyperparameters, architectures, and ML config against the improved code

## Core Concept

Responsibility is divided strictly:

| Responsibility | Owner |
|---|---|
| What to explore (search space design) | Agents |
| Running trials, parallelism, search strategy | WandB Sweeps |
| Compute provisioning | SkyPilot + Vast.ai |
| Metric tracking and result storage | WandB |
| Campaign state, iteration logic, learnings | This skill |

**Project contract (the only coupling):**
- The target project must have a training entrypoint that reads hyperparameters from `wandb.config`
- The target project must log metrics to WandB via `wandb.log(...)`

No other coupling. The skill is framework-agnostic at the interface level, though PyTorch Lightning projects satisfy this contract naturally.

## State Machine

```
LOAD_CAMPAIGN
  → HYDRATE_STATE
    → IDEATE
      → WAIT_FOR_PROPOSALS
        → SELECT_AND_DESIGN_SWEEP
          → LOCAL_SANITY
            → LAUNCH_SWEEP
              → WAIT_FOR_SWEEP
                → ANALYZE
                  → ROLL_ITERATION
                    → COMPLETE | BLOCKED_CONFIG | BLOCKED_PROTOCOL | FAILED
                    → IDEATE (next iteration)
```

### State Descriptions

**`LOAD_CAMPAIGN`** — governed by `metaopt-load-campaign`. Validates campaign YAML, checks preflight readiness artifact. Transitions to `BLOCKED_CONFIG` on invalid config or missing preflight.

**`HYDRATE_STATE`** — governed by `metaopt-hydrate-state`. Initializes or resumes state. If `state.current_sweep.sweep_id` exists, reconnects to that WandB sweep rather than launching a new one (crash recovery). Verifies `skypilot-wandb-worker` availability.

**`IDEATE`** — governed by `metaopt-background-control`. Background agents (`metaopt-ideation-worker`) continuously generate sweep search space proposals. Each proposal includes a WandB-formatted sweep config (parameter distributions, method). Agents run until proposal pool reaches threshold.

**`WAIT_FOR_PROPOSALS`** — governed by `metaopt-background-control`. Gate: require `proposal_policy.current_target` proposals before advancing to selection.

**`SELECT_AND_DESIGN_SWEEP`** — governed by `metaopt-select-design`. Single agent picks the best proposal from the pool given prior learnings and baseline context, then refines it into a final WandB sweep config ready for launch. Select and design are one step — no separate `DESIGN_EXPERIMENT` state.

**`LOCAL_SANITY`** — time-bounded smoke test, **maximum 60 seconds, hardcoded, not configurable**. Goal: confirm the training script starts, loads data, and executes a forward+backward pass without crashing. Not a correctness check — a crash-detection gate. If the script has not crashed within 60 seconds, it passes. Failure here means `FAILED` — do not launch, do not spend GPU budget.

**`LAUNCH_SWEEP`** — governed by `metaopt-remote-execution-control`. Dispatches `skypilot-wandb-worker` to create the WandB sweep and launch SkyPilot agents on Vast.ai pointing at `wandb agent <sweep_id>`. Always launches with `--idle-minutes-to-autostop` set to `compute.idle_timeout_minutes` as a safety net.

**`WAIT_FOR_SWEEP`** — governed by `metaopt-remote-execution-control`. Polls `skypilot-wandb-worker` for sweep status. Each poll also acts as a watchdog: detect hung agents (no WandB logs for `idle_timeout_minutes`), kill their SkyPilot jobs, enforce budget cap. Transitions to `ANALYZE` when sweep completes, to `FAILED` if all agents crash.

**`ANALYZE`** — governed by `metaopt-remote-execution-control`. Dispatches `metaopt-analysis-worker` to read the best WandB run from the sweep, compare against baseline, update baseline if improved, extract learnings.

**`ROLL_ITERATION`** — governed by `metaopt-iteration-close-control`. Filters `next_proposals` for the next iteration, increments iteration counter, checks all stop conditions, emits iteration report, transitions to `COMPLETE` or back to `IDEATE`.

### Terminal States

- `COMPLETE` — stop condition met; emit final report, remove `AGENTS.md` hook, stop.
- `BLOCKED_CONFIG` — user-actionable config issue (budget cap hit, bad campaign YAML, preflight failed); preserve state, remove hook, stop.
- `BLOCKED_PROTOCOL` — protocol-level violation the skill cannot recover from; preserve state, remove hook, stop with descriptive `next_action`.
- `FAILED` — unrecoverable error (LOCAL_SANITY failed, all sweep agents crashed); preserve state, remove hook, stop.

## Campaign YAML

```yaml
campaign:
  name: my-ml-campaign
  description: "Optimize GNN hyperparameters for MNIST accuracy"

project:
  repo: git@github.com:user/project.git
  smoke_test_command: "python train.py --smoke"   # must not crash within 60s

wandb:
  entity: my-entity
  project: my-wandb-project

compute:
  provider: vast_ai                  # resolved by SkyPilot
  accelerator: A100:1
  num_sweep_agents: 4               # parallel WandB agents per sweep
  idle_timeout_minutes: 15          # kill agent if no WandB logs for this long
  max_budget_usd: 10                # hard spend cap — default is 10 USD

objective:
  metric: val/accuracy
  direction: maximize               # maximize | minimize
  improvement_threshold: 0.005     # minimum gain to count as improvement

proposal_policy:
  current_target: 5                 # proposals needed before selection

stop_conditions:
  max_iterations: 20
  target_metric: 0.99
  max_no_improve_iterations: 5
  # max_budget_usd is under compute — budget enforcement is part of watchdog
```

## Backend Contract

All remote execution goes through `skypilot-wandb-worker`. Three operations:

### `launch_sweep`
- Input: WandB sweep config (method, parameters, metric), SkyPilot task spec (repo, accelerator, num agents), `idle_timeout_minutes`
- Action: (1) calls WandB API to register the sweep, (2) runs `sky launch --idle-minutes-to-autostop <idle_timeout_minutes>` for each agent pointing at `wandb agent <sweep_id>`
- Output: `{ "sweep_id": "...", "sweep_url": "...", "sky_job_ids": [...], "launched_at": "..." }`

Both steps are atomic within the worker. If `sky launch` fails after sweep creation, the worker cancels the sweep via WandB API before returning an error.

### `poll_sweep`
- Input: `sweep_id`, `sky_job_ids`, `idle_timeout_minutes`, `max_budget_usd`, `cumulative_spend_usd`
- Action:
  1. Query WandB API for sweep status and best run metric
  2. Check each active run's last log timestamp — if `> idle_timeout_minutes`, kill via `sky down <job_id>`, mark WandB run as crashed
  3. Query SkyPilot cost estimate — if cumulative spend `>= max_budget_usd`, kill all jobs, return budget-exceeded status
- Output:
  ```json
  {
    "sweep_status": "running | completed | failed | budget_exceeded",
    "best_metric_value": 0.934,
    "best_run_id": "wandb-run-abc",
    "killed_runs": ["run-xyz"],
    "cumulative_spend_usd": 3.40
  }
  ```

**Forbidden:** raw SSH, direct Vast.ai API calls, any cluster operation not mediated by SkyPilot or WandB API. If the worker cannot represent a needed operation, fail closed to `BLOCKED_PROTOCOL`.

## Instance Lifecycle Management

Idle and hung instances are detected and killed on every `poll_sweep` call:

1. **Idle detection**: `now - last_wandb_log_timestamp > idle_timeout_minutes` → hung
2. **Kill**: `sky down <job_id>` → mark WandB run as crashed via API
3. **Budget gate**: cumulative spend tracked in state; if cap reached → kill all, `BLOCKED_CONFIG`
4. **Safety net**: every `sky launch` includes `--idle-minutes-to-autostop <idle_timeout_minutes>` so instances self-terminate even if the skill crashes mid-session

**Crash recovery**: if the skill crashes mid-sweep, instances self-terminate via the autostop safety net. On resume, `HYDRATE_STATE` detects `current_sweep.sweep_id` in state and reconnects to the existing WandB sweep. SkyPilot job IDs in `state.current_sweep.sky_job_ids` resume watchdog monitoring.

## Agents & Workers

### Removed (vs. v3)
- `hetzner-delegation-worker` — replaced by `skypilot-wandb-worker`
- `metaopt-design-worker` — merged into `metaopt-select-design`
- `metaopt-materialization-worker` — not needed (no code patches)
- `metaopt-diagnosis-worker` — not needed (no local sanity remediation)
- `metaopt-rollover-worker` — absorbed into `metaopt-iteration-close-control`
- `metaopt-local-execution-control` — replaced by simple LOCAL_SANITY directive

### Retained / Redesigned

| Agent | Role | Status |
|---|---|---|
| `metaopt-ideation-worker` | Proposes sweep search spaces | Redesigned — output is a WandB sweep config, not a code proposal |
| `metaopt-select-design` | Picks best proposal, finalizes sweep config | Redesigned — one agent, one step (select + design merged) |
| `metaopt-analysis-worker` | Reads WandB best run, updates baseline, extracts learnings | Updated to read WandB API output |
| `metaopt-background-control` | Manages ideation pool | Simplified — no maintenance mode |
| `metaopt-iteration-close-control` | Rolls iteration, checks stop conditions | Absorbs rollover filtering |
| `skypilot-wandb-worker` | Creates sweep, launches agents, polls + watchdog | New leaf worker |

### Ideation Worker Output

Each proposal is a structured sweep search space:

```json
{
  "proposal_id": "prop-001",
  "rationale": "Exploring residual connections — prior learnings show deeper networks overfit without skip connections",
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

WandB receives this config directly. Agents design the search space; WandB searches it.

## State File

`.ml-metaopt/state.json` key fields:

```json
{
  "campaign_identity_hash": "...",
  "machine_state": "WAIT_FOR_SWEEP",
  "status": "RUNNING",
  "current_iteration": 3,
  "baseline": {
    "metric": "val/accuracy",
    "value": 0.923,
    "wandb_run_id": "abc123",
    "wandb_run_url": "https://wandb.ai/...",
    "established_at": "2026-04-13T10:00:00Z"
  },
  "current_sweep": {
    "sweep_id": "wandb-sweep-xyz",
    "sweep_url": "https://wandb.ai/...",
    "sky_job_ids": ["sky-job-1", "sky-job-2"],
    "launched_at": "2026-04-13T11:00:00Z",
    "cumulative_spend_usd": 3.40
  },
  "current_proposals": [],
  "next_proposals": [],
  "key_learnings": [],
  "completed_iterations": [],
  "no_improve_iterations": 1
}
```

**Removed from state (vs. v3):** `active_slots`, `proposal_cycle.ideation_rounds_by_slot`, `selected_experiment.design`, `selected_experiment.diagnosis_history`, `local_changeset`, `apply_results`, `runtime_capabilities.available_skills` (simplified to a single worker check).

## Control Protocol

The handoff envelope is simplified. One directive per handoff:

```json
{
  "recommended_next_machine_state": "LAUNCH_SWEEP",
  "state_patch": { "...": "..." },
  "directive": {
    "type": "create_sweep | launch_agents | poll_sweep | run_smoke_test | none",
    "payload": { "...": "..." }
  }
}
```

No pre/post directive lists. No launch_requests arrays. The orchestrator executes the single directive, writes the result to `.ml-metaopt/worker-results/`, and re-invokes the control agent.

## Stop Conditions

Evaluated by `metaopt-iteration-close-control` after each iteration:

| Condition | Transition |
|---|---|
| best metric meets `target_metric` (direction-aware: `>=` for maximize, `<=` for minimize) | `COMPLETE` |
| `current_iteration >= max_iterations` | `COMPLETE` |
| `no_improve_iterations >= max_no_improve_iterations` | `COMPLETE` |
| `cumulative_spend_usd >= max_budget_usd` | `BLOCKED_CONFIG` — "budget cap reached" |
| All sweep agents crashed with no successful runs | `FAILED` |
| `LOCAL_SANITY` timed out or crashed | `FAILED` — no GPU spend |

## Final Report

Written to `.ml-metaopt/final_report.md` on `COMPLETE`:

- Best run: WandB run URL, metric value, sweep config that produced it
- Iteration history: per-iteration best metric, spend, proposal rationale
- Total cumulative spend (USD)
- Key learnings accumulated across iterations
- Stop condition that triggered completion

## What This Removes vs. v3

| Removed | Replaced by |
|---|---|
| Custom queue scripts (`enqueue_batch.py`, etc.) | WandB API + SkyPilot CLI |
| Hetzner Ray cluster | SkyPilot + Vast.ai |
| Slot accounting (`active_slots`, background/auxiliary limits) | WandB sweep parallelism |
| Executor event files | Single `worker-results/` file per directive |
| Patch materialization + conflict resolution | Not in scope (separate skill) |
| `QUIESCE_SLOTS` state | WandB sweep lifecycle |
| 3-attempt LOCAL_SANITY remediation loop | 60-second smoke test, fail fast |
| Complex directive lists (`pre_launch_directives`, `post_launch_directives`) | Single directive per handoff |
| `metaopt-diagnosis-worker` | Not needed without patch remediation |
| 6 control agent files | 4 control agents (select+design merged, rollover absorbed) |

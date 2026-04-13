# Backend Contract

## Purpose

All remote execution goes through `skypilot-wandb-worker`. The skill never calls SkyPilot or WandB APIs directly — only the worker does.

The orchestrator dispatches `skypilot-wandb-worker` for execution directives emitted by control agents. The worker executes the operation, writes its result to `.ml-metaopt/worker-results/<name>.json`, and exits. The control agent reads the result file in its subsequent phase.

## Operations

### `launch_sweep`

**Directive type:** `launch_sweep`

**Input payload:**
- `sweep_config` — valid WandB sweep config object (`method`, `metric`, `parameters`)
- `wandb_entity` — WandB entity name
- `wandb_project` — WandB project name
- `repo` — target project git URL
- `accelerator` — compute spec (e.g. `A100:1`)
- `num_sweep_agents` — number of parallel WandB agents to launch (1–16)
- `idle_timeout_minutes` — SkyPilot autostop timeout (5–60)

**Worker execution:**
1. Call WandB API: `wandb.sweep(sweep_config, project=wandb_project, entity=wandb_entity)` — returns `sweep_id`
2. For each of `num_sweep_agents` agents: `sky launch --idle-minutes-to-autostop <idle_timeout_minutes>` running `wandb agent <entity>/<project>/<sweep_id>`
3. If any `sky launch` fails after sweep creation, cancel the sweep via WandB API before returning an error (atomic guarantee)

**Result file:** `.ml-metaopt/worker-results/launch-sweep.json`

```json
{
  "sweep_id": "abc123",
  "sweep_url": "https://wandb.ai/entity/project/sweeps/abc123",
  "sky_job_ids": ["sky-job-1", "sky-job-2", "sky-job-3", "sky-job-4"],
  "launched_at": "2026-04-13T11:00:00Z"
}
```

### `poll_sweep`

**Directive type:** `poll_sweep`

**Input payload:**
- `sweep_id` — WandB sweep to poll
- `sky_job_ids` — SkyPilot job identifiers to monitor
- `idle_timeout_minutes` — threshold for hung agent detection
- `max_budget_usd` — hard spend cap
- `cumulative_spend_usd` — spend so far (from state)

**Worker execution:**
1. Query WandB API for sweep status and best run metric value
2. For each active run: check `last_log_at` timestamp. If `now - last_log_at > idle_timeout_minutes`, call `sky down <job_id>` and mark the run as crashed via WandB API
3. Query SkyPilot for cumulative cost estimate. If `cumulative_spend_usd >= max_budget_usd`, kill all remaining jobs via `sky down`, return `budget_exceeded` status

**Result file:** `.ml-metaopt/worker-results/poll-sweep.json`

```json
{
  "sweep_status": "running",
  "best_metric_value": 0.934,
  "best_run_id": "wandb-run-abc",
  "best_run_url": "https://wandb.ai/entity/project/runs/wandb-run-abc",
  "best_run_config": {"learning_rate": 0.001, "batch_size": 64, "hidden_dim": 256},
  "killed_runs": ["run-xyz"],
  "cumulative_spend_usd": 3.40
}
```

`sweep_status` values: `running`, `completed`, `failed`, `budget_exceeded`

`best_run_config` contains the hyperparameter configuration of the best run (from WandB run config). The `metaopt-analysis-worker` can use this directly without fetching from WandB separately.

### `run_smoke_test`

**Directive type:** `run_smoke_test`

**Input payload:**
- `command` — the smoke test command from `project.smoke_test_command`
- `repo` — target project git URL (for cloning if needed)

**Worker execution:**
1. Run `command` locally (or on a cheap CPU instance if `project.repo` is a remote URL)
2. Enforce **60-second hard timeout** — not configurable
3. If the command has not crashed within 60 seconds, it passes

**Result file:** `.ml-metaopt/worker-results/smoke-test.json`

```json
{
  "exit_code": 0,
  "timed_out": false,
  "stdout_tail": "...",
  "stderr_tail": "..."
}
```

## Forbidden Operations

The following are protocol breaches that trigger `BLOCKED_PROTOCOL`:

- Raw SSH to any instance
- Direct Vast.ai API calls (bypassing SkyPilot)
- `sky exec` (use `sky launch` only)
- `ray job submit` or any Ray CLI command
- Any cluster operation not mediated by SkyPilot or WandB API
- Orchestrator calling WandB API or SkyPilot CLI directly (must go through `skypilot-wandb-worker`)

If the worker cannot represent a needed operation through the three declared operations above, it must fail closed. The orchestrator transitions to `BLOCKED_PROTOCOL`.

## Instance Lifecycle Contract

- Every `sky launch` includes `--idle-minutes-to-autostop <idle_timeout_minutes>` — instances self-terminate if the skill crashes mid-session
- On resume after crash, `HYDRATE_STATE` detects `current_sweep.sweep_id` in state and reconnects to the existing WandB sweep
- SkyPilot job IDs in `state.current_sweep.sky_job_ids` resume watchdog monitoring via subsequent `poll_sweep` calls
- Never launch a new sweep if `current_sweep.sweep_id` already exists in state — always reconnect

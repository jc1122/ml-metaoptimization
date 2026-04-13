# Contracts

This document defines the data contracts for campaign state, handoff envelopes, worker results, and identity hashing. For the control-agent handoff protocol and state-patch ownership rules, see `references/control-protocol.md`.

## Section 1 — State File Schema

Path: `.ml-metaopt/state.json`

| Field | Type | Constraint |
|-------|------|------------|
| `version` | integer | Must be `4` |
| `campaign_id` | string | Non-empty |
| `campaign_identity_hash` | string | Format: `sha256:<64 lowercase hex chars>` |
| `status` | string | One of: `RUNNING`, `BLOCKED_CONFIG`, `BLOCKED_PROTOCOL`, `FAILED`, `COMPLETE` |
| `machine_state` | string | One of the valid machine states (see `references/state-machine.md`) |
| `current_iteration` | integer | `>= 0` |
| `next_action` | string or null | Human-readable recovery hint when terminal; null when running |
| `objective_snapshot` | object | Frozen copy of campaign objective (see below) |
| `proposal_cycle` | object | Current proposal cycle metadata (see below) |
| `current_sweep` | object or null | Active WandB sweep (see below); null when no sweep is running |
| `selected_sweep` | object or null | Sweep config chosen for launch (see below); null until selection |
| `baseline` | object or null | Current best result (see below); null until first improvement |
| `current_proposals` | list[proposal] | Candidate pool for next selection |
| `next_proposals` | list[proposal] | Proposals generated while a sweep is running |
| `key_learnings` | list[string] | Accumulated insights across iterations |
| `completed_iterations` | list[iteration_record] | History of finished iterations |
| `no_improve_iterations` | integer | `>= 0`; consecutive iterations without baseline improvement |
| `campaign_started_at` | string | ISO 8601 timestamp; set during HYDRATE_STATE on fresh init |

### `objective_snapshot` object

| Field | Type | Constraint |
|-------|------|------------|
| `metric` | string | Non-empty (e.g. `"val/accuracy"`) |
| `direction` | string | `"maximize"` or `"minimize"` |
| `improvement_threshold` | float | `> 0` |

### `proposal_cycle` object

| Field | Type | Constraint |
|-------|------|------------|
| `cycle_id` | string | Format: `iter-<N>-cycle-<M>` |
| `current_pool_frozen` | boolean | `true` once selection begins; `false` during ideation |

### `current_sweep` object (when non-null)

| Field | Type | Constraint |
|-------|------|------------|
| `sweep_id` | string | WandB sweep identifier |
| `sweep_url` | string | Full WandB sweep URL |
| `sky_job_ids` | list[string] | SkyPilot job identifiers for launched agents |
| `launched_at` | string | ISO 8601 timestamp |
| `cumulative_spend_usd` | float | `>= 0`; starts at `0.0` when a sweep is launched; updated on each `poll_sweep` result |

### `selected_sweep` object (when non-null)

| Field | Type | Constraint |
|-------|------|------------|
| `proposal_id` | string | The winning proposal's identifier |
| `sweep_config` | object | Valid WandB sweep config with `method`, `metric`, `parameters` |

### `baseline` object (when non-null)

| Field | Type | Constraint |
|-------|------|------------|
| `metric` | string | Same as `objective_snapshot.metric` |
| `value` | float | Best metric value observed |
| `wandb_run_id` | string | WandB run identifier |
| `wandb_run_url` | string | Full WandB run URL |
| `established_at` | string | ISO 8601 timestamp |

### `proposal` object

Each entry in `current_proposals` or `next_proposals`:

| Field | Type | Constraint |
|-------|------|------------|
| `proposal_id` | string | Non-empty, unique within the campaign |
| `rationale` | string | Why this search space is expected to improve the metric |
| `sweep_config` | object | Valid WandB sweep config with `method`, `metric`, `parameters` |

### `iteration_record` object

Each entry in `completed_iterations`:

| Field | Type | Constraint |
|-------|------|------------|
| `iteration` | integer | `>= 1` |
| `sweep_id` | string | WandB sweep identifier for this iteration |
| `best_metric_value` | float | Best metric from the sweep |
| `spend_usd` | float | `>= 0`; cost of this iteration |
| `improved_baseline` | boolean | Whether baseline was updated |

### Status semantics

- `status` is the coarse lifecycle summary derived from `machine_state` when state is persisted
- Control agents do not write `status` directly in `state_patch`
- Allowed pairings:
  - `status = RUNNING` with any non-terminal `machine_state`
  - `status = BLOCKED_CONFIG` only with `machine_state = BLOCKED_CONFIG`
  - `status = BLOCKED_PROTOCOL` only with `machine_state = BLOCKED_PROTOCOL`
  - `status = FAILED` only with `machine_state = FAILED`
  - `status = COMPLETE` only with `machine_state = COMPLETE`

## Section 2 — Handoff Envelope Schema

Every control-agent handoff must be a JSON object written to:

```
.ml-metaopt/handoffs/<agent-name>-<machine_state>.json
```

Schema:

```json
{
  "recommended_next_machine_state": "LAUNCH_SWEEP or null",
  "state_patch": { "selected_sweep": { "..." } },
  "directive": {
    "type": "launch_sweep",
    "payload": { "sweep_config": { "..." } }
  },
  "launch_requests": []
}
```

| Field | Type | Constraint |
|-------|------|------------|
| `recommended_next_machine_state` | string or null | `null` = stay in current state (poll again); non-null must be a valid machine state |
| `state_patch` | object | Keys must match `STATE_PATCH_OWNERSHIP` for the invoking agent |
| `directive.type` | string | One of: `launch_sweep`, `poll_sweep`, `run_smoke_test`, `none` |
| `directive.payload` | object | Action-specific fields (see `references/backend-contract.md` for execution directives) |
| `launch_requests` | list[WorkerLaunchRequest] | Optional; workers to dispatch. Each entry: `{ "skill": "<worker_ref>", "payload": { ... }, "result_file": "<path>" }` |

### `WorkerLaunchRequest` object

Each entry in `launch_requests`:

| Field | Type | Constraint |
|-------|------|------------|
| `skill` / `worker_ref` | string | The worker skill name (e.g. `"metaopt-ideation-worker"`, `"metaopt-analysis-worker"`) |
| `payload` | object | Worker-specific input payload; contents vary by worker |
| `result_file` | string | Path where the worker writes its output JSON (must be under `.ml-metaopt/worker-results/`) |
| `slot_class` | string | Optional; `"background"` or `"auxiliary"` — determines concurrency policy |
| `mode` | string | Optional; must be present if `slot_class` is present (e.g. `"ideation"`, `"analysis"`) |
| `model_class` | string | Required; model tier for the worker (e.g. `"general_worker"`, `"strong_reasoner"`) |
| `task_file` | string | Required; path to the task description file the worker should read |
| `preferred_model` | string | Auto-injected by `normalize_launch_requests`; resolved from `model_class` |

## Section 3 — Worker Result File Schema

All worker and directive results are written to:

```
.ml-metaopt/worker-results/<name>.json
```

No other output path is valid. Each result file is a single JSON object. The `name` component identifies the operation (e.g. `smoke-test`, `launch-sweep`, `poll-sweep`, `analysis-iter-3`).

Workers must not write results to handoff paths, task paths, or arbitrary locations.

## Section 4 — Identity Hash Computation

`campaign_identity_hash` is computed as follows:

1. Extract the `campaign`, `project`, `wandb`, and `objective` top-level fields from `ml_metaopt_campaign.yaml`
2. Serialize as canonical JSON: sorted keys, compact separators (`","`, `":"`), `ensure_ascii=true`
3. Encode the JSON string as UTF-8 bytes
4. Hash with SHA-256
5. Store as `sha256:<64 lowercase hex chars>`

Identity mismatches on resume must block (`BLOCKED_CONFIG`) rather than silently reinitializing state. Edits to `compute`, `proposal_policy`, or `stop_conditions` do not change the identity hash and must not discard progress.

## Section 5 — Baseline Comparison

Baseline comparison is direction-aware using `objective_snapshot`:

- **maximize:** new value improves baseline if `new_value > baseline.value + improvement_threshold`
- **minimize:** new value improves baseline if `new_value < baseline.value - improvement_threshold`

When improvement is detected:
- Update `baseline` with the new run's data
- Reset `no_improve_iterations` to `0`

When no improvement:
- Increment `no_improve_iterations` by `1`
- Preserve existing `baseline` unchanged

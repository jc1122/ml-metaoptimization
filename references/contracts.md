# Contracts

## Campaign Spec

`ml_metaopt_campaign.yaml` is the declarative source of truth.

Required fields:
- `version`
- `campaign_id`
- `goal`
- `objective.metric`
- `objective.direction`
- `objective.aggregation`
- `objective.improvement_threshold`
- `datasets`
- `baseline.aggregate`
- `baseline.by_dataset`
- `stop_conditions`
- `stop_conditions.max_wallclock_hours`
- `proposal_policy`
- `dispatch_policy`
- `sanity`
- `artifacts`
- `remote_queue`
- `remote_queue.backend`
- `remote_queue.retry_policy`
- `execution`
- `execution.entrypoint`

Use `ml_metaopt_campaign.example.yaml` as the canonical example.

Command-valued fields in the campaign spec are shell command strings executed via the runtime shell.
They must therefore be written exactly as runnable shell commands, with absolute or shell-resolvable paths.
Sentinel placeholders such as angle-bracket paths, `YOUR_*`, and dataset fingerprints containing `replace-me` are invalid and must force `BLOCKED_CONFIG`.

## Campaign Identity Hash Contract

The v3 contract separates campaign identity from runtime configuration:

- `campaign_identity_hash`: canonical JSON over `version`, `campaign_id`, `objective.metric`, `objective.direction`, `objective.aggregation`, and the sorted dataset entries `{id, role, fingerprint}`
- `runtime_config_hash`: canonical JSON over `sanity`, `artifacts`, `remote_queue`, and `execution`

Hash canonicalization rules:
- ignore comments and YAML key order
- serialize the selected fields as canonical JSON with sorted keys
- hash the canonical JSON bytes with SHA-256 and store as `sha256:<64 lowercase hex chars>`

Edits outside the identity payload must not discard progress. Identity mismatches must block resume instead of silently reinitializing state.

## State File

Path: `.ml-metaopt/state.json`

Authoritative responsibilities:
- resume source
- slot status
- current and next proposal pools
- selected experiment
- local artifact packaging
- remote batch lifecycle
- iteration counters and learnings

Required top-level keys:
- `version`
- `campaign_id`
- `campaign_identity_hash`
- `runtime_config_hash`
- `status`
- `machine_state`
- `current_iteration`
- `next_action`
- `objective_snapshot`
- `proposal_cycle`
- `active_slots`
- `current_proposals`
- `next_proposals`
- `selected_experiment`
- `local_changeset`
- `remote_batches`
- `baseline`
- `completed_experiments`
- `key_learnings`
- `no_improve_iterations`

`proposal_cycle` must record:
- `cycle_id`
- `current_pool_frozen`
- `ideation_rounds_by_slot` as a map of non-empty `slot_id` strings to non-negative integer round counts
- `shortfall_reason`

Each `remote_batches[]` entry must be an object with:
- `batch_id`
- `queue_ref`
- `status` using one of `queued`, `running`, `completed`, or `failed`

Recommended additional keys when useful:
- `event_log_tail`
- `sanity_attempts_current_experiment`
- `maintenance_summary`
- `last_iteration_report`

Status semantics:
- `status` is the coarse lifecycle summary: `RUNNING`, `BLOCKED_CONFIG`, `FAILED`, or `COMPLETE`
- `machine_state` is the authoritative control-flow state from the state machine
- allowed pairings:
  - `status = RUNNING` with any non-terminal `machine_state`
  - `status = BLOCKED_CONFIG` only with `machine_state = BLOCKED_CONFIG`
  - `status = FAILED` only with `machine_state = FAILED`
  - `status = COMPLETE` only with `machine_state = COMPLETE`

## Proposal Pools

- `current_proposals`: candidate pool eligible for the next selection decision
- `next_proposals`: proposals generated while a batch is running or after `current_proposals` has been frozen

Never write new ideas into `current_proposals` once `SELECT_EXPERIMENT` begins.

## Slot Contract

### Dispatch Types

The orchestrator dispatches worker skills in two ways:

**Slot-based dispatch** — used for ideation, maintenance, synthesis, design, materialization, diagnosis, and analysis. The orchestrator creates an entry in `active_slots` with the appropriate `slot_class`, `mode`, and `model_class`. The slot persists until the subagent completes and the orchestrator harvests its output. Slot-based workers are subject to `dispatch_policy.background_slots` and `dispatch_policy.auxiliary_slots` limits.

**Inline dispatch** — used for rollover during `ROLL_ITERATION`. The orchestrator launches the subagent synchronously, consumes the output immediately, and advances to the next state. No `active_slots` entry is created and the dispatch does not count against slot limits.

The `mode` values listed below apply only to slot-based dispatch.

Each active slot must record:
- `slot_id`
- `slot_class`
- `mode`
- `model_class`
- `requested_model`
- `resolved_model`
- `status`
- `attempt`
- `task_summary`

`slot_id` and `status` must be non-empty strings. `attempt` must be a positive integer.

`slot_class` values:
- `background`
- `auxiliary`

`mode` values:
- `ideation`
- `maintenance`
- `synthesis`
- `design`
- `materialization`
- `diagnosis`
- `analysis`

`mode = materialization` requires `model_class = strong_coder`.

## Selected Experiment Contract

`selected_experiment` may be `null` until `SELECT_EXPERIMENT` persists a winner; once selected, it is an object with `proposal_id` and `sanity_attempts`.

When present, `selected_experiment` must be an object with:
- `proposal_id` as a non-empty string
- `sanity_attempts` as a non-negative integer

## Local Changeset Contract

`local_changeset` may be `null` until `MATERIALIZE_CHANGESET` persists outputs; once present, it is an object with the documented fields.

When present, each `local_changeset` must record:
- `integration_worktree`
- `patch_artifacts`
- `apply_results`
- `verification_notes`
- `code_artifact_uri`
- `data_manifest_uri`

When present, each `patch_artifacts[]` entry must be an object with:
- `producer_slot_id`
- `purpose`
- `patch_path`
- `target_worktree`

When present, each `apply_results[]` entry must be an object with:
- `patch_path`
- `status`

## Batch Manifest Contract

The orchestrator enqueues exactly one immutable batch manifest per experiment batch.

The orchestrator owns `batch_id` generation. It must write `batch_id` into the manifest before enqueueing. The backend must echo the same `batch_id` in all command responses and must not mint a different identifier.

Required manifest fields:
- `version`
- `campaign_id`
- `iteration`
- `batch_id`
- `experiment`
- `retry_policy`
- `artifacts.code_artifact.uri`
- `artifacts.data_manifest.uri`
- `execution.entrypoint`

Minimal manifest shape:

```json
{
  "version": 3,
  "campaign_id": "<campaign_id>",
  "iteration": 3,
  "batch_id": "<batch_id>",
  "experiment": {
    "proposal_id": "<proposal_id>"
  },
  "retry_policy": {
    "max_attempts": 2
  },
  "artifacts": {
    "code_artifact": {
      "uri": "<code artifact uri>"
    },
    "data_manifest": {
      "uri": "<data manifest uri>"
    }
  },
  "execution": {
    "entrypoint": "<command>"
  }
}
```

## Batch Status / Result Contract

The backend must persist machine-readable status with:
- `batch_id`
- lifecycle `status`
- timestamps
- observed utilization when available
- best aggregate result when completed
- per-dataset results when completed
- artifact locations
- failure classification when failed

## Aggregate Baseline Contract

- `baseline.aggregate` is the authoritative campaign score
- `baseline.aggregate` must be numeric
- `baseline.by_dataset` is diagnostic but mandatory
- `baseline.by_dataset` must be a non-empty object mapping non-empty dataset ids to numeric values
- `objective.direction` determines improvement checks
- `objective.aggregation` determines how dataset scores roll up into the aggregate
- `objective.improvement_threshold` determines whether an iteration counts as an improvement
- when the new aggregate meets or exceeds the improvement threshold in the configured direction, reset `no_improve_iterations` to `0`
- otherwise increment `no_improve_iterations` by `1`

## Iteration Report Contract

At the end of `ROLL_ITERATION`, after carry-over filtering is complete, emit:

```text
=== Iteration <N> Report ===
Experiment batch:       <batch_id>
Baseline before:        <metric> = <aggregate value>
Baseline after:         <metric> = <aggregate value> (<+/- delta>)
Per-dataset scores:     <dataset=value pairs>
Key learnings:          <new learnings this iteration>
Carry-over proposals:   <count after filtering>
Maintenance work done:  <summary>
Next action:            <next_action>
```

## Final Report Contract

On `COMPLETE`, emit:

```text
=== Final Campaign Report ===
Campaign:               <campaign_id>
Iterations completed:   <count>
Baseline start:         <metric> = <aggregate value>
Baseline finish:        <metric> = <aggregate value> (<+/- delta>)
Best per-dataset scores:<dataset=value pairs>
Top learnings:          <ranked summary>
Final status:           COMPLETE
```

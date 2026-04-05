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
- `execution`
- `execution.entrypoint`

Use `ml_metaopt_campaign.example.yaml` as the canonical example.

Command-valued fields in the campaign spec are shell command strings executed via the runtime shell.
They must therefore be written exactly as runnable shell commands, with absolute or shell-resolvable paths.
Sentinel placeholders such as angle-bracket paths, `YOUR_*`, and dataset fingerprints containing `replace-me` are invalid and must force `BLOCKED_CONFIG`.

## Campaign Identity Hash Contract

The v2 contract separates campaign identity from runtime configuration:

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

Each active slot must record:
- `slot_id`
- `slot_class`
- `mode`
- `model`
- `status`
- `attempt`
- `task_summary`

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

## Batch Manifest Contract

The orchestrator enqueues exactly one immutable batch manifest per experiment batch.

The orchestrator owns `batch_id` generation. It must write `batch_id` into the manifest before enqueueing. The backend must echo the same `batch_id` in all command responses and must not mint a different identifier.

Required manifest fields:
- `version`
- `campaign_id`
- `iteration`
- `batch_id`
- `experiment`
- `artifacts.code_artifact.uri`
- `execution.entrypoint`

Expected additional fields:
- objective metadata
- data manifest or fingerprints
- retry policy
- results contract

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
- `baseline.by_dataset` is diagnostic but mandatory
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

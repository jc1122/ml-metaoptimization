# Contracts

This document defines the data contracts for campaign specs, state files, manifests, and results. For the control-agent handoff protocol and state-patch ownership rules, see `references/control-protocol.md`.

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
- serialize the selected fields as canonical JSON with sorted keys, compact separators `(",", ":")`, and `ensure_ascii=true`
- encode the resulting JSON string as UTF-8 bytes
- hash the UTF-8 bytes with SHA-256 and store as `sha256:<64 lowercase hex chars>`

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
- `maintenance_summary` — array of finding strings. Initialized to `[]` on fresh state. If absent on an existing state (legacy states), `metaopt-background-control` creates the key on first write via `state_patch`.
- `campaign_started_at` — ISO 8601 timestamp recording when the campaign was first initialized. Set during `HYDRATE_STATE` on fresh initialization; preserved across resumes. Used by `metaopt-iteration-close-control` to evaluate the `max_wallclock_hours` stop condition. When absent on resume (legacy states), hydration defaults it to the current timestamp.
- `runtime_capabilities`

`last_iteration_report` is written by `metaopt-iteration-close-control` after each iteration closes; absent until the first iteration completes. Once present, it is an object whose shape is defined by the iteration-report contract below.

`runtime_capabilities` must record:
- `verified_at` as an ISO 8601 timestamp string
- `available_skills` as an array of non-empty strings
- `missing_skills` as an array of strings (may be empty)
- `degraded_lanes` as an array of strings (may be empty)

`proposal_cycle` must record:
- `cycle_id`
- `current_pool_frozen`
- `ideation_rounds_by_slot` as a map of non-empty `slot_id` strings to non-negative integer round counts
- `shortfall_reason` — valid values:
  - `"not_enough_proposals"` — pool has fewer than `proposal_policy.current_target`
  - `"floor_not_met"` — pool has fewer than `proposal_policy.current_floor` proposals (early-advance threshold not met)
  - `"all_proposals_attempted"` — all current proposals have already been run
  - `""` (empty string) — no shortfall
- `pool_saturated_iteration` — integer or `null`. Set to `state.current_iteration` when any ideation worker signals `saturated: true` for the current iteration; `null` otherwise. Cleared by `metaopt-background-control` on the next plan invocation for a different iteration.

Each `remote_batches[]` entry must be an object with:
- `batch_id`
- `queue_ref`
- `status` using one of `queued`, `running`, `completed`, or `failed`

Optional keys written by specific control agents when relevant:
- `pending_remote_batch` — written by `metaopt-remote-execution-control` during the remote batch lifecycle; absent outside that lifecycle
- `event_log_tail` — diagnostic tail written by backend-facing control agents; no defined owner
- `sanity_attempts_current_experiment` — informational counter; no defined owner

Status semantics:
- `status` is the coarse lifecycle summary: `RUNNING`, `BLOCKED_CONFIG`, `BLOCKED_PROTOCOL`, `FAILED`, or `COMPLETE`
- `machine_state` is the authoritative control-flow state from the state machine
- `status` is derived from `machine_state` when state is persisted; control agents do not write it directly in `state_patch`
- allowed pairings:
  - `status = RUNNING` with any non-terminal `machine_state`
  - `status = BLOCKED_CONFIG` only with `machine_state = BLOCKED_CONFIG`
  - `status = BLOCKED_PROTOCOL` only with `machine_state = BLOCKED_PROTOCOL`
  - `status = FAILED` only with `machine_state = FAILED`
  - `status = COMPLETE` only with `machine_state = COMPLETE`

## Proposal Pools

- `current_proposals`: candidate pool eligible for the next selection decision
- `next_proposals`: proposals generated while a batch is running or after `current_proposals` has been frozen

Never write new ideas into `current_proposals` once `SELECT_EXPERIMENT` begins.

### Proposal Record Shape

Each proposal record in `current_proposals` or `next_proposals` must contain:

**Control-plane enrichment fields** (set by `metaopt-background-control` via `state_patch` during its gate phase, NOT by the leaf ideation worker):
- `proposal_id`: non-empty string, unique within the campaign. Generated by `metaopt-background-control` using `<campaign_id>-p<sequence_number>` format.
- `source_slot_id`: non-empty string identifying the slot that generated this proposal
- `creation_iteration`: positive integer — the iteration number when the proposal was created
- `created_at`: ISO 8601 timestamp

**Worker-provided fields** (returned by the Step-3 ideation worker):
- `title`: string, concise name (≤ 12 words)
- `rationale`: string, why this change is expected to improve the metric
- `expected_impact`: object with `direction` (`"improve"` | `"neutral"`) and `magnitude` (`"small"` | `"medium"` | `"large"`)
- `target_area`: string, one of the allowed target area values

`metaopt-background-control` must enrich every ideation candidate with the control-plane enrichment fields via `state_patch` before appending to a pool. Leaf workers never generate `proposal_id` — that is the governing control agent's responsibility.

When the rollover worker returns a merged proposal, the merged result contains only worker-provided fields (`title`, `rationale`, `expected_impact`, `target_area`). `metaopt-iteration-close-control` enriches it with a new `proposal_id`, `source_slot_id = "rollover"`, `creation_iteration` set to the new iteration number, and `created_at` via `state_patch` before appending to `current_proposals`.

When `metaopt-selection-worker` receives `current_proposals`, every proposal already has all fields above. Selection returns the winning proposal object unchanged.

When the rollover worker receives `next_proposals`, every proposal has all fields above. Carry-over proposals preserve all fields unchanged.

## Slot Contract

### Dispatch Types

The orchestrator dispatches worker targets in two ways:

**Slot-based dispatch** — used for ideation, maintenance, selection, design, materialization, diagnosis, and analysis. The orchestrator creates an entry in `active_slots` with the appropriate `slot_class`, `mode`, and `model_class`. The slot persists until the subagent completes and the orchestrator harvests its output. Slot-based workers are subject to `dispatch_policy.background_slots` and `dispatch_policy.auxiliary_slots` limits.

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
- `selection`
- `design`
- `materialization`
- `diagnosis`
- `analysis`

`mode = materialization` requires `model_class = strong_coder`.

## Selected Experiment Contract

`selected_experiment` may be `null` until `SELECT_EXPERIMENT` persists a winner. Once set, it is the authoritative handoff object for the current experiment across all states from `SELECT_EXPERIMENT` through `ANALYZE_RESULTS`.

When present, `selected_experiment` must be an object with:

**Set by SELECT_EXPERIMENT:**
- `proposal_id`: non-empty string — the winning proposal's identifier
- `proposal_snapshot`: object — a frozen copy of the full proposal record (all control-plane enrichment fields and worker-provided fields from the Proposal Record Shape)
- `selection_rationale`: string — the ranking rationale from the selection worker
- `sanity_attempts`: non-negative integer — initialized to `0`

**Set by DESIGN_EXPERIMENT:**
- `design`: object or `null` — the full experiment design returned by the design worker. `null` until DESIGN_EXPERIMENT completes. Once set, this is the authoritative input for MATERIALIZE_CHANGESET.

**Set by LOCAL_SANITY (on failure):**
- `diagnosis_history`: array — ordered list of diagnosis records from the shared diagnosis worker. Empty array until a sanity failure occurs. Each entry is an object with:
  - `attempt`: positive integer
  - `root_cause`: string
  - `classification`: string (one of `code_error`, `config_error`, `data_error`, `infra_error`, `design_error`)
  - `action`: string (`"fix"`, `"adjust_config"`, or `"abandon"`)
  - `code_guidance`: string or `null`
  - `config_guidance`: string or `null`
  - `diagnosed_at`: ISO 8601 timestamp

**Set by ANALYZE_RESULTS:**
- `analysis_summary`: object or `null` — the structured analysis output. `null` until ANALYZE_RESULTS completes. When set, contains:
  - `judgment`: string (`"improvement"`, `"regression"`, or `"neutral"`)
  - `new_aggregate`: number — the post-experiment aggregate score
  - `delta`: number — signed change from baseline
  - `learnings`: array of strings — new key insights
  - `invalidations`: array — proposal invalidation recommendations
  - `carry_over_candidates`: array — aspects worth further exploration

`metaopt-iteration-close-control` clears `selected_experiment` (sets it to `null`) via `state_patch` during `ROLL_ITERATION` after persisting the completed experiment to `completed_experiments`. The orchestrator applies this `state_patch` mechanically.

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

When present, `verification_notes` is a list of strings, each a human-readable note about a verification step (e.g. `"pytest passed"`, `"mypy: 0 errors"`).

When present, each `apply_results[]` entry must be an object with:
- `patch_path`: string — path to the patch artifact
- `status`: one of `"applied"`, `"failed"`, or `"skipped"`
- `error`: string or `null` — error message when `status` is `"failed"`, `null` otherwise

Example entry:
```json
{"patch_path": "...", "status": "applied", "error": null}
```

## Batch Manifest Contract

One immutable batch manifest is enqueued per experiment batch. The orchestrator writes the manifest file from the `write_manifest` executor directive emitted by `metaopt-remote-execution-control`. The control agent then calls `enqueue_command` itself via the `hetzner-delegation` skill — `enqueue_batch` is not an orchestrator executor directive.

`batch_id` is generated by `metaopt-remote-execution-control` and provided to the orchestrator in the `write_manifest` executor directive. The orchestrator writes it into the manifest verbatim. The backend must echo the same `batch_id` in all command responses and must not mint a different identifier.

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

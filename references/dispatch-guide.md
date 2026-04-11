# Dispatch Guide

This reference maps each dispatch state in the state machine to the worker target that executes it, the context the orchestrator must pass, and the output it must consume.

The orchestrator is a transport and runtime shell. It launches workers from pre-staged task files written by control agents, stages raw worker outputs, and re-invokes the governing control agent to gate results. It does not construct prompt envelopes inline or make semantic dispatch decisions. See `references/control-protocol.md` for the control-handoff protocol.

The orchestrator must not infer executor work from prose in this document. When executor-side work is required, the governing control agent emits explicit `executor_directives`; when no executor-side work is required, the handoff must still emit `executor_directives = []`.

Load this file before dispatching any worker subagent.

## Dispatch Types

### Slot-Based Dispatch
Used for ideation, maintenance, selection, design, materialization, diagnosis, and analysis. The orchestrator creates a slot entry in `active_slots` with the appropriate `slot_class`, `mode`, and `model_class` before launching the subagent.

### Inline Dispatch
Used for rollover. `metaopt-iteration-close-control` emits a `launch_requests` entry for `metaopt-rollover-worker` without `slot_class` or `mode`; the absence of `slot_class` signals inline dispatch to the orchestrator. The orchestrator launches the worker synchronously. No slot entry is created in `active_slots`. The subagent returns before the orchestrator advances to the next state. Because rollover is inline-only, `mode = "rollover"` is not a valid auxiliary slot mode — attempts to dispatch rollover as a slot-based worker are rejected by the guardrail.

### Launch Request Model Hints

Every `launch_requests` entry may include a `preferred_model` field — a deterministic model hint specifying which model the orchestrator should use for the launch. The guardrail utility `normalize_launch_requests()` adds `preferred_model` automatically when absent:
- `strong_reasoner` → `claude-opus-4.6` (or the highest available opus ≥ 4.6)
- `strong_coder` → `claude-opus-4.6` (or the highest available opus ≥ 4.6)
- `general_worker` → `claude-sonnet-4`

The `preferred_model` is a deterministic launch parameter, not an excuse for semantic fallback. If the preferred model is unavailable, the orchestrator takes the next configured fallback for that model class (`gpt-5.4`, or the highest available gpt ≥ 5.4) and records the substitution in the slot metadata (`requested_model` vs `resolved_model`).

### Artifact Preconditions and `BLOCKED_PROTOCOL`

Certain dispatch states require worker artifacts from prior phases as preconditions. If the required artifacts are missing, the control agent must fail closed to `BLOCKED_PROTOCOL` rather than allowing the orchestrator to improvise:

- **Remediation** (during `LOCAL_SANITY`): requires `diagnosis-worker` output artifact. If the diagnosis artifact is missing, `metaopt-local-execution-control` transitions to `BLOCKED_PROTOCOL`.
- **Result judgment** (during `ANALYZE_RESULTS`): requires `analysis-worker` output artifact and remote results payload. If either is missing, `metaopt-remote-execution-control` transitions to `BLOCKED_PROTOCOL`.

## Prompt Envelope

Every worker subagent prompt includes a standard envelope plus state-specific fields. Control agents write the envelope into staged task files; the orchestrator passes these task files to workers without modifying semantic content.

### Standard Envelope (included in every dispatch)

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `campaign_id` | string | `campaign.campaign_id` | Campaign identifier |
| `current_iteration` | integer | `state.current_iteration` | Current iteration number |
| `slot_id` | string | slot metadata | The slot ID dispatching this worker (omitted for inline dispatch) |
| `attempt` | integer | slot metadata | Attempt number for this dispatch (1-indexed; omitted for inline dispatch) |

### Normalized Objective Fields

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `goal` | string | `campaign.goal` | Campaign improvement goal |
| `metric` | string | `campaign.objective.metric` | Target metric name |
| `direction` | string | `campaign.objective.direction` | `"minimize"` or `"maximize"` |
| `aggregation_method` | string | `campaign.objective.aggregation.method` | e.g. `"weighted_mean"`, `"mean"` |
| `aggregation_weights` | object or null | `campaign.objective.aggregation.weights` | Per-dataset weights when method is `weighted_mean`; `null` otherwise |
| `improvement_threshold` | number | `campaign.objective.improvement_threshold` | Minimum delta to qualify as improvement |

### Normalized Baseline Fields

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `aggregate_baseline` | number | `state.baseline.aggregate` | Current aggregate baseline |
| `per_dataset_baselines` | object | `state.baseline.by_dataset` | Per-dataset baselines |

### Normalized Execution Fields (included when relevant)

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `runner_type` | string | `campaign.execution.runner_type` | e.g. `"ray_queue_runner"` |
| `entrypoint` | string | `campaign.execution.entrypoint` | Shell command |
| `trial_budget` | object | `campaign.execution.trial_budget` | `{ kind: string, value: number }` — passed as-is |
| `search_strategy` | object | `campaign.execution.search_strategy` | `{ kind: string, ...params }` — passed as-is |

### Normalized History Fields (included when relevant)

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `key_learnings` | array | `state.key_learnings` | Learnings from prior iterations |
| `completed_experiments` | array | `state.completed_experiments` | Prior experiment records |

## MAINTAIN_BACKGROUND_POOL — Ideation

This phase is governed by `metaopt-background-control`:
- The control agent generates staged task files for each slot launch (`plan_background_work`)
- The orchestrator launches workers from those task files without interpreting the intended work
- The control agent consumes staged worker outputs and performs the canonical state updates described below (`gate_background_work`)

**Worker target:** `metaopt-ideation-worker` (`custom_agent`)
**Slot class:** `background`
**Mode:** `ideation`
**Model class:** `general_worker`

### Input (from orchestrator context)

> Includes standard envelope fields plus normalized objective, baseline, and history fields (see Prompt Envelope above).

| Field | Source |
|-------|--------|
| `current_proposal_pool` | `state.current_proposals` |
| `next_proposal_pool_context` | `state.next_proposals` |
| `proposal_policy` | `campaign.proposal_policy` |

### Output → State

- For each candidate returned by the ideation worker, the background-control gate:
  1. Generates a unique `proposal_id` using `<campaign_id>-p<sequence_number>`
  2. Attaches `source_slot_id` from the dispatching slot
  3. Attaches `creation_iteration` from `state.current_iteration`
  4. Attaches `created_at` timestamp
  5. Appends the enriched proposal record to `state.current_proposals` (if `proposal_cycle.current_pool_frozen == false`) or `state.next_proposals` (if frozen)
- If worker returns `{ "saturated": true }`, switch slot to maintenance mode
- Increment `state.proposal_cycle.ideation_rounds_by_slot[slot_id]`

## MAINTAIN_BACKGROUND_POOL — Maintenance

This phase is governed by `metaopt-background-control`:
- The control agent generates staged task files for maintenance launches
- The orchestrator launches workers from those task files and stages raw outputs
- The control agent semantically integrates staged worker outputs during the gate phase

**Worker target:** `repo-audit-refactor-optimize` (`skill`)
**Slot class:** `background`
**Mode:** `maintenance`
**Model class:** `general_worker` (findings-only) or `strong_coder` (code-modifying)

### Input

| Field | Source |
|-------|--------|
| Project codebase | Isolated worktree |
| Focus areas | Campaign goal context, prior maintenance summaries |
| Patch artifact contract | `references/worker-lanes.md` Maintenance Lane |

### Output → State

- Findings-only: append findings summary to `state.maintenance_summary`
- Code-modifying: write one unified diff patch artifact to `.ml-metaopt/artifacts/patches/`; patch is NOT applied during background work — the orchestrator records the patch path as an executor event and defers integration to `QUIESCE_SLOTS` via `apply_patch_artifacts` directives from `metaopt-iteration-close-control`

### Task File Bridge

Because `repo-audit-refactor-optimize` is a generic skill, `metaopt-background-control` must embed the following context in every staged maintenance task file it writes:
- The patch artifact contract from `references/worker-lanes.md` (unified diff format, required metadata fields)
- The target worktree path
- The campaign goal context for focus area selection
- An explicit instruction to produce either findings-only output or one unified diff patch with the required metadata fields

The orchestrator passes this task file to the worker without modification. If the maintenance worker returns output that does not match the expected patch artifact shape, the `metaopt-background-control` gate phase treats it as findings-only and appends to `state.maintenance_summary`.

## SELECT_EXPERIMENT

This state is governed by `metaopt-select-design`:
- The control agent writes a staged selection task (`plan_select_experiment`)
- The orchestrator launches `metaopt-selection-worker` and stages raw output
- The control agent validates the winning proposal and plans `DESIGN_EXPERIMENT` (`gate_select_and_plan_design`)

**Worker target:** `metaopt-selection-worker` (`custom_agent`)
**Slot class:** `auxiliary`
**Mode:** `selection`
**Model class:** `strong_reasoner`

### Input (from orchestrator context)

> Includes standard envelope fields plus normalized objective, baseline, and history fields (see Prompt Envelope above).

| Field | Source |
|-------|--------|
| `current_proposals` | `state.current_proposals` (frozen) |
| `proposal_policy` | `campaign.proposal_policy` |

### Output → State

- Write `state.selected_experiment = { proposal_id: <winner.proposal_id>, proposal_snapshot: <full proposal object>, selection_rationale: <ranking_rationale>, sanity_attempts: 0, design: null, diagnosis_history: [], analysis_summary: null }`
- Set `state.proposal_cycle.current_pool_frozen = true`

## DESIGN_EXPERIMENT

This state is governed by `metaopt-select-design`:
- `DESIGN_EXPERIMENT` begins with a partial canonical `selected_experiment` whose `design` is still `null`
- The control agent writes a staged design task, the orchestrator launches `metaopt-design-worker`, and the control agent finalizes `state.selected_experiment.design` (`finalize_select_design`)

**Worker target:** `metaopt-design-worker` (`custom_agent`)
**Slot class:** `auxiliary`
**Mode:** `design`
**Model class:** `strong_reasoner`

### Input (from orchestrator context)

> Includes standard envelope fields plus normalized objective, baseline, and history fields (see Prompt Envelope above).

| Field | Source |
|-------|--------|
| `winning_proposal` | The full proposal object from `state.current_proposals` matching `state.selected_experiment.proposal_id` |
| `datasets` | `campaign.datasets` |
| `execution` | `campaign.execution` |
| `backend_contract` | Summary of `references/backend-contract.md` enqueue/status/results requirements |

### Output → State

- Persist the full experiment design in `state.selected_experiment.design`
- The design is the authoritative input for `MATERIALIZE_CHANGESET`

## MATERIALIZE_CHANGESET

**Worker target:** `metaopt-materialization-worker` (`custom_agent`)
**Slot class:** `auxiliary`
**Mode:** `materialization`
**Model class:** `strong_coder` (enforced: `mode = materialization` requires `model_class = strong_coder`)

This state is governed by `metaopt-local-execution-control`:
- The control agent writes a staged materialization task file, emits a `launch_requests` entry for `metaopt-materialization-worker`, and emits an `apply_patch_artifacts` directive (with `output_event_path` set) ordering the orchestrator to attempt mechanical patch integration after the worker finishes (`plan_local_changeset`)
- The orchestrator launches the worker; once the worker completes, it executes the `apply_patch_artifacts` directive — attempting mechanical patch integration and writing the outcome (success or conflict details) as an executor event at `output_event_path`
- The orchestrator re-invokes the control agent in `gate_materialization` phase; the control agent reads the integration outcome executor event and either emits a conflict-resolution `launch_requests` entry (merge failed) or advances to `LOCAL_SANITY` (merge succeeded)
- After `LOCAL_SANITY`, the control agent gates sanity results and routes retries or advances (`gate_local_sanity`)

### Input (from orchestrator context)

The materialization worker operates in one of three modes. `metaopt-local-execution-control` embeds `materialization_mode` in the staged task file it writes; the orchestrator passes the task file to the worker without modification.

> `materialization_mode` is a dispatch-specific parameter passed in the worker subagent prompt; it is not part of the Standard Envelope. Valid values: `"standard"`, `"remediation"`, `"conflict_resolution"`.

**Standard mode** (`materialization_mode: "standard"` — during `MATERIALIZE_CHANGESET`):

| Field | Source |
|-------|--------|
| Experiment design specification | `state.selected_experiment.design` |
| Campaign config | `campaign.artifacts` (code_roots, data_roots, exclude), `campaign.execution` |
| Project codebase | Isolated worktree (created by orchestrator) |
| Key learnings | `state.key_learnings` |

**Remediation mode** (`materialization_mode: "remediation"` — during `LOCAL_SANITY` after diagnosis):

| Field | Source |
|-------|--------|
| `code_guidance` | From diagnosis `fix_recommendation.code_guidance` |
| Original experiment design | `state.selected_experiment.design` |
| Current local changeset | `state.local_changeset` |
| Diagnosis history | `state.selected_experiment.diagnosis_history` |
| Project codebase | Isolated worktree with current patch applied |

**Conflict-resolution mode** (`materialization_mode: "conflict_resolution"` — when mechanical patch integration fails):

| Field | Source |
|-------|--------|
| Conflicting patches | The patches that failed to apply cleanly |
| Base worktree state | The integration worktree at the point of conflict |
| Experiment design context | `state.selected_experiment.design` (for intent understanding) |
| Key learnings | `state.key_learnings` |

### Output → State

- Write unified diff patch to `.ml-metaopt/artifacts/patches/`
- Populate `state.local_changeset.patch_artifacts[]` with `{ producer_slot_id, purpose, patch_path, target_worktree }`
- Write sanity verification notes to `state.local_changeset.verification_notes`
- Orchestrator executes `package_code_artifact` directive and writes the resulting URI to `output_event_path`; control agent gate phase reads it and emits `code_artifact_uri` in `state_patch`
- Orchestrator executes `package_data_manifest` directive and writes the resulting URI to `output_event_path`; control agent gate phase reads it and emits `data_manifest_uri` in `state_patch`
- Orchestrator executes `write_manifest` directive and writes the batch manifest to the path specified in the directive

## LOCAL_SANITY — Diagnosis (on failure)

**Worker target:** `metaopt-diagnosis-worker` (`custom_agent`)
**Slot class:** `auxiliary`
**Mode:** `diagnosis`
**Model class:** `strong_reasoner`

This state is governed by `metaopt-local-execution-control`:
- The orchestrator runs `sanity.command` and stages raw stdout/stderr/exit-code artifacts
- The control agent interprets those staged artifacts, requests diagnosis via a staged task file, and performs the canonical state updates described below (`gate_local_sanity`)

### Input (from orchestrator context)

| Field | Source |
|-------|--------|
| `failure_context` | Captured stdout, stderr, exit_code from `sanity.command` |
| `experiment_design` | Design from `DESIGN_EXPERIMENT` output |
| `code_changes` | Patch summary from materialization output |
| `sanity_config` | `campaign.sanity` |
| `previous_diagnoses` | Prior diagnosis outputs for this experiment (if any) |
| `attempt_number` | `state.selected_experiment.sanity_attempts` |
| `max_attempts` | 3 (hardcoded cap) |

### Output → State

- Persist the diagnosis record to `state.selected_experiment.diagnosis_history` with `attempt`, `root_cause`, `classification`, `action`, `code_guidance`, `config_guidance`, and `diagnosed_at`
- Increment `state.selected_experiment.sanity_attempts`
- Route on `fix_recommendation.action`:
  - `"fix"`: dispatch `metaopt-materialization-worker` in **remediation mode** — pass the `code_guidance`, the original `state.selected_experiment.design`, the current `state.local_changeset`, and the `diagnosis_history`. The materialization worker produces an updated patch. After integration, rerun `LOCAL_SANITY`.
  - `"adjust_config"`: transition to `BLOCKED_CONFIG` with `next_action = <config_guidance>`. The orchestrator does not modify campaign configuration autonomously.
  - `"abandon"`: transition to `FAILED` with `root_cause` as the terminal error.

## WAIT_FOR_REMOTE_BATCH — Remote Failure Diagnosis

**Worker target:** `metaopt-diagnosis-worker` (`custom_agent`)
**Slot class:** `auxiliary`
**Mode:** `diagnosis`
**Model class:** `strong_reasoner`

This state is governed by `metaopt-remote-execution-control`:
- The control agent emits `queue_op` directives for `status_command`; the orchestrator dispatches `@hetzner-delegation-worker` and writes raw backend JSON payloads to `.ml-metaopt/queue-results/status-<batch_id>.json`
- The control agent reads those payloads, requests diagnosis via a staged task file, and performs the canonical state updates described below (`gate_remote_batch`)

Dispatched only when `remote_queue.status_command` returns `status = "failed"`.

### Input (from orchestrator context)

| Field | Source |
|-------|--------|
| `failure_context` | Remote failure payload: `{ classification, message, returncode }` from `status_command` response |
| `experiment_design` | `state.selected_experiment.design` |
| `code_changes` | Patch summary from `state.local_changeset` |
| `sanity_config` | `campaign.sanity` |
| `previous_diagnoses` | `state.selected_experiment.diagnosis_history` |
| `attempt_number` | `state.selected_experiment.sanity_attempts` |
| `max_attempts` | 3 (hardcoded cap) |

### Output → State

- Persist diagnosis record to `state.selected_experiment.diagnosis_history`
- Append learnings from diagnosis to `state.key_learnings` (remote failures always generate learnings even without reaching ANALYZE_RESULTS)
- Route on `fix_recommendation.action`:
  - `"fix"`: transition to `FAILED` — remote code failures cannot be patched and re-run without a full re-enqueue cycle
  - `"adjust_config"`: transition to `BLOCKED_CONFIG` with `next_action = <config_guidance>`
  - `"abandon"`: transition to `FAILED` with `root_cause` as terminal error
- Remote retries are the backend's responsibility via `remote_queue.retry_policy`. The orchestrator never re-enqueues a failed batch.

## ANALYZE_RESULTS

**Worker target:** `metaopt-analysis-worker` (`custom_agent`)
**Slot class:** `auxiliary`
**Mode:** `analysis`
**Model class:** `strong_reasoner`

This state is governed by `metaopt-remote-execution-control`:
- The control agent emits a `queue_op` directive for `results_command`; the orchestrator dispatches `@hetzner-delegation-worker` and writes the completed results payload to `.ml-metaopt/queue-results/results-<batch_id>.json`
- The control agent reads that file, stages an analysis task file (`analyze_remote_results`), and the orchestrator launches `metaopt-analysis-worker`
- The control agent updates `analysis_summary`, baseline state, learnings, and rollover readiness

### Input (from orchestrator context)

> Includes standard envelope fields plus normalized objective, baseline, and history fields (see Prompt Envelope above).

| Field | Source |
|-------|--------|
| `batch_results` | Raw JSON object from `.ml-metaopt/queue-results/results-<batch_id>.json` (written by the orchestrator after dispatching `@hetzner-delegation-worker` for `results_command`) and passed as `batch_results` in the subagent prompt. Minimum expected keys: `batch_id`, `status`, `trials` (list), `summary_metrics`. |
| Experiment context | Selected experiment design + winning proposal |

### Output → State

- If judgment `improvement`: update `state.baseline.aggregate` and `state.baseline.by_dataset`, set `state.no_improve_iterations = 0`
- If judgment `regression` or `neutral`: leave baseline unchanged, increment `state.no_improve_iterations`
- Append returned learnings to `state.key_learnings`
- Append experiment record to `state.completed_experiments`
- Carry proposal invalidations and carry-over candidates forward to `ROLL_ITERATION`
- Persist the structured analysis in `state.selected_experiment.analysis_summary` with `judgment`, `new_aggregate`, `delta`, `learnings`, `invalidations`, and `carry_over_candidates`

## ROLL_ITERATION — Rollover

**Worker target:** `metaopt-rollover-worker` (`custom_agent`)
**Dispatch type:** Inline (no slot — runs synchronously during `ROLL_ITERATION`)
**Model class:** `strong_reasoner`

This state is governed by `metaopt-iteration-close-control`:
- The control agent writes a staged rollover task file (`plan_roll_iteration`)
- The orchestrator launches `metaopt-rollover-worker` and stages raw JSON output
- The control agent semantically integrates that output, clears `selected_experiment`, evaluates stop conditions, and prepares `QUIESCE_SLOTS` (`gate_roll_iteration`)

### Input (from orchestrator context)

> Includes standard envelope fields plus normalized objective, baseline, and history fields (see Prompt Envelope above).

| Field | Source |
|-------|--------|
| `next_proposals` | `state.next_proposals` |
| Results analysis output | Output from `ANALYZE_RESULTS` (judgment, learnings, invalidations, carry-over candidates) |
| `proposal_policy` | `campaign.proposal_policy` |
| Stop conditions progress | `state.current_iteration`, `state.no_improve_iterations`, `campaign.stop_conditions` |

### Output → State

- Move filtered carry-over proposals into `state.current_proposals`
- For merged proposals, enrich the merged candidate with a new `proposal_id` (using `<campaign_id>-p<sequence_number>`), set `source_slot_id = "rollover"` and `creation_iteration` to the new iteration, then append to `state.current_proposals`
- Clear `state.next_proposals`
- If `needs_fresh_ideation == true`, `metaopt-background-control` prioritizes ideation slots in its next `MAINTAIN_BACKGROUND_POOL` plan phase
- Increment `state.current_iteration` only when the campaign will continue into another iteration
- Clear `state.selected_experiment` after persisting the completed experiment record
- Evaluate stop conditions against `target_metric`, `max_iterations`, and `max_no_improve_iterations`
- Emit `state.last_iteration_report` before entering `QUIESCE_SLOTS`

# Dispatch Guide

This reference maps each dispatch state in the state machine to the worker skill that executes it, the context the orchestrator must pass, and the output it must consume.

Load this file before dispatching any worker subagent.

## Dispatch Types

### Slot-Based Dispatch
Used for ideation, maintenance, synthesis, design, materialization, diagnosis, and analysis. The orchestrator creates a slot entry in `active_slots` with the appropriate `slot_class`, `mode`, and `model_class` before launching the subagent.

### Inline Dispatch
Used for rollover. The orchestrator launches the subagent synchronously during a state transition. No slot entry is created in `active_slots`. The subagent returns before the orchestrator advances to the next state.

## Prompt Envelope

Every worker subagent prompt includes a standard envelope plus state-specific fields. The orchestrator builds the envelope by normalizing campaign and state data into a flat, unambiguous shape.

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

**Skill:** `metaopt-experiment-ideation`
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

- For each candidate returned by the ideation worker, the orchestrator:
  1. Generates a unique `proposal_id` using `<campaign_id>-p<sequence_number>`
  2. Attaches `source_slot_id` from the dispatching slot
  3. Attaches `creation_iteration` from `state.current_iteration`
  4. Attaches `created_at` timestamp
  5. Appends the enriched proposal record to `state.current_proposals` (if `proposal_cycle.current_pool_frozen == false`) or `state.next_proposals` (if frozen)
- If worker returns `{ "saturated": true }`, switch slot to maintenance mode
- Increment `state.proposal_cycle.ideation_rounds_by_slot[slot_id]`

## MAINTAIN_BACKGROUND_POOL — Maintenance

**Skill:** `repo-audit-refactor-optimize`
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
- Code-modifying: write one unified diff patch artifact to `.ml-metaopt/artifacts/patches/`; orchestrator integrates mechanically

## SELECT_EXPERIMENT

**Skill:** `metaopt-experiment-selection`
**Slot class:** `auxiliary`
**Mode:** `synthesis`
**Model class:** `strong_reasoner`

### Input (from orchestrator context)

> Includes standard envelope fields plus normalized objective, baseline, and history fields (see Prompt Envelope above).

| Field | Source |
|-------|--------|
| `current_proposals` | `state.current_proposals` (frozen) |
| `proposal_policy` | `campaign.proposal_policy` |

### Output → State

- Write `state.selected_experiment = { proposal_id: <winner.proposal_id>, sanity_attempts: 0 }`
- Set `state.proposal_cycle.current_pool_frozen = true`

## DESIGN_EXPERIMENT

**Skill:** `metaopt-experiment-design`
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

- Persist the full experiment design in `state.selected_experiment.design` (or a dedicated state field)
- The design becomes the input for `MATERIALIZE_CHANGESET`

## MATERIALIZE_CHANGESET

**Skill:** `metaopt-experiment-materialization`
**Slot class:** `auxiliary`
**Mode:** `materialization`
**Model class:** `strong_coder` (enforced: `mode = materialization` requires `model_class = strong_coder`)

### Input (from orchestrator context)

| Field | Source |
|-------|--------|
| Experiment design specification | Output from `DESIGN_EXPERIMENT` |
| Campaign config | `campaign.artifacts` (code_roots, data_roots, exclude), `campaign.execution` |
| Project codebase | Isolated worktree (created by orchestrator) |
| Key learnings | `state.key_learnings` |

### Output → State

- Write unified diff patch to `.ml-metaopt/artifacts/patches/`
- Populate `state.local_changeset.patch_artifacts[]` with `{ producer_slot_id, purpose, patch_path, target_worktree }`
- Write sanity verification notes to `state.local_changeset.verification_notes`
- Orchestrator packages code artifact → `state.local_changeset.code_artifact_uri`
- Orchestrator packages data manifest → `state.local_changeset.data_manifest_uri`
- Orchestrator writes batch manifest → `.ml-metaopt/artifacts/manifests/`

## LOCAL_SANITY — Diagnosis (on failure)

**Skill:** `metaopt-sanity-diagnosis`
**Slot class:** `auxiliary`
**Mode:** `diagnosis`
**Model class:** `strong_reasoner`

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

- If `action == "fix"`: orchestrator dispatches materialization (or applies code_guidance inline) and reruns `LOCAL_SANITY`
- If `action == "abandon"`: orchestrator transitions to `FAILED`
- Increment `state.selected_experiment.sanity_attempts`

## ANALYZE_RESULTS

**Skill:** `metaopt-results-analysis`
**Slot class:** `auxiliary`
**Mode:** `analysis`
**Model class:** `strong_reasoner`

### Input (from orchestrator context)

> Includes standard envelope fields plus normalized objective, baseline, and history fields (see Prompt Envelope above).

| Field | Source |
|-------|--------|
| Batch results payload | stdout JSON from `remote_queue.results_command <batch_id>` |
| Experiment context | Selected experiment design + winning proposal |

### Output → State

- If judgment `improvement`: update `state.baseline.aggregate` and `state.baseline.by_dataset`, set `state.no_improve_iterations = 0`
- If judgment `regression` or `neutral`: leave baseline unchanged, increment `state.no_improve_iterations`
- Append returned learnings to `state.key_learnings`
- Append experiment record to `state.completed_experiments`
- Carry proposal invalidations and carry-over candidates forward to `ROLL_ITERATION`

## ROLL_ITERATION — Rollover

**Skill:** `metaopt-proposal-rollover`
**Dispatch type:** Inline (no slot — runs synchronously during `ROLL_ITERATION`)
**Model class:** `strong_reasoner`

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
- Clear `state.next_proposals`
- If `needs_fresh_ideation == true`, the orchestrator prioritizes ideation in the next `MAINTAIN_BACKGROUND_POOL` entry
- Increment `state.current_iteration`

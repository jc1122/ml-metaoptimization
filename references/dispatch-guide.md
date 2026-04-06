# Dispatch Guide

This reference maps each dispatch state in the state machine to the worker skill that executes it, the context the orchestrator must pass, and the output it must consume.

Load this file before dispatching any worker subagent.

## Dispatch Types

### Slot-Based Dispatch
Used for ideation, maintenance, synthesis, design, materialization, diagnosis, and analysis. The orchestrator creates a slot entry in `active_slots` with the appropriate `slot_class`, `mode`, and `model_class` before launching the subagent.

### Inline Dispatch
Used for rollover. The orchestrator launches the subagent synchronously during a state transition. No slot entry is created in `active_slots`. The subagent returns before the orchestrator advances to the next state.

## MAINTAIN_BACKGROUND_POOL — Ideation

**Skill:** `metaopt-experiment-ideation`
**Slot class:** `background`
**Mode:** `ideation`
**Model class:** `general_worker`

### Input (from orchestrator context)

| Field | Source |
|-------|--------|
| `goal` | `campaign.goal` |
| `metric` | `campaign.objective.metric` |
| `objective_direction` | `campaign.objective.direction` |
| `aggregation` | `campaign.objective.aggregation` (serialize method + weights) |
| `aggregate_baseline` | `state.baseline.aggregate` |
| `per_dataset_baselines` | `state.baseline.by_dataset` |
| `key_learnings` | `state.key_learnings` |
| `completed_experiments` | `state.completed_experiments` |
| `current_proposal_pool` | `state.current_proposals` |
| `next_proposal_pool_context` | `state.next_proposals` |
| `proposal_policy` | `campaign.proposal_policy` |

### Output → State

- Append returned proposals to `state.current_proposals` (if `proposal_cycle.current_pool_frozen == false`) or `state.next_proposals` (if frozen)
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

| Field | Source |
|-------|--------|
| `goal` | `campaign.goal` |
| `metric` | `campaign.objective.metric` |
| `direction` | `campaign.objective.direction` |
| `aggregation` | `campaign.objective.aggregation` |
| `aggregate_baseline` | `state.baseline.aggregate` |
| `per_dataset_baselines` | `state.baseline.by_dataset` |
| `current_proposals` | `state.current_proposals` (frozen) |
| `key_learnings` | `state.key_learnings` |
| `completed_experiments` | `state.completed_experiments` |
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

| Field | Source |
|-------|--------|
| `winning_proposal` | The full proposal object from `state.current_proposals` matching `state.selected_experiment.proposal_id` |
| `goal` | `campaign.goal` |
| `metric` | `campaign.objective.metric` |
| `direction` | `campaign.objective.direction` |
| `aggregation` | `campaign.objective.aggregation` |
| `datasets` | `campaign.datasets` |
| `execution` | `campaign.execution` |
| `aggregate_baseline` | `state.baseline.aggregate` |
| `per_dataset_baselines` | `state.baseline.by_dataset` |
| `key_learnings` | `state.key_learnings` |
| `completed_experiments` | `state.completed_experiments` |
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

| Field | Source |
|-------|--------|
| Batch results payload | stdout JSON from `remote_queue.results_command <batch_id>` |
| `metric` | `campaign.objective.metric` |
| `direction` | `campaign.objective.direction` |
| `aggregation` | `campaign.objective.aggregation` |
| `weights` | `campaign.objective.aggregation.weights` (when weighted_mean) |
| `improvement_threshold` | `campaign.objective.improvement_threshold` |
| `aggregate` | `state.baseline.aggregate` |
| `by_dataset` | `state.baseline.by_dataset` |
| Experiment context | Selected experiment design + winning proposal |
| `key_learnings` | `state.key_learnings` |
| `completed_experiments` | `state.completed_experiments` |

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

| Field | Source |
|-------|--------|
| `next_proposals` | `state.next_proposals` |
| Results analysis output | Output from `ANALYZE_RESULTS` (judgment, learnings, invalidations, carry-over candidates) |
| `key_learnings` | `state.key_learnings` (already updated by ANALYZE_RESULTS) |
| `completed_experiments` | `state.completed_experiments` (already updated by ANALYZE_RESULTS) |
| Campaign goal | `campaign.goal`, `campaign.objective.metric`, `campaign.objective.direction`, `campaign.objective.aggregation` |
| `proposal_policy` | `campaign.proposal_policy` |
| Stop conditions progress | `state.current_iteration`, `state.no_improve_iterations`, `campaign.stop_conditions` |

### Output → State

- Move filtered carry-over proposals into `state.current_proposals`
- Clear `state.next_proposals`
- If `needs_fresh_ideation == true`, the orchestrator prioritizes ideation in the next `MAINTAIN_BACKGROUND_POOL` entry
- Increment `state.current_iteration`

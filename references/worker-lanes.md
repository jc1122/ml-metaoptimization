# Worker Lanes

## Slot Classes

Background slots:
- `ideation`
- `maintenance`

Auxiliary slots:
- `synthesis`
- `design`
- `materialization`
- `diagnosis`
- `analysis`

## Model Classes

Model names in the campaign spec and SKILL.md are examples — use the strongest available model in the same class if a listed name is unavailable or superseded:
- `strong_coder`: code changes, debugging, conflict resolution
- `strong_reasoner`: synthesis, experiment design, diagnosis, result analysis
- `general_worker`: ideation and findings-only maintenance work

Always use the strongest available model in the same class and record the substitution in state.

## Ideation Lane

**Skill:** `metaopt-experiment-ideation`

Purpose:
- generate and refine non-overlapping experiment proposals

Inputs:
- goal
- metric
- aggregate baseline
- key learnings
- completed experiments
- current and next proposal pool context

Outputs:
- distinct proposal candidates with short rationale

## Maintenance Lane

**Skill:** `repo-audit-refactor-optimize`

Purpose:
- audit and improve the local repo while proposals accumulate or proposal pools saturate

Default requirement:
- maintenance workers must invoke `repo-audit-refactor-optimize`

Expected maintenance focus areas:
- leakage audit
- test gaps and determinism
- pipeline correctness
- data loading efficiency
- code quality issues
- profiling and speed risks

Execution rules:
- all maintenance slots dispatch in parallel, subject to the global background-slot count
- use isolated worktrees
- do not interfere with the orchestrator working tree
- findings-only maintenance may use `general_worker`
- code-modifying maintenance must use `strong_coder`
- return either findings-only output or one patch artifact plus verification notes
- the orchestrator applies maintenance patch artifacts mechanically in a dedicated integration worktree
- if patch application conflicts or requires a non-trivial merge, dispatch `metaopt-experiment-materialization` in conflict-resolution mode with the conflicting patches, the base worktree state, and the experiment design context

Metaoptimization bridge requirements:
- The orchestrator must include the patch artifact contract (format, metadata fields, and integration path) in the maintenance worker's subagent prompt, because `repo-audit-refactor-optimize` does not natively encode these requirements
- Maintenance workers must be told to emit one unified diff patch artifact with `producer_slot_id`, `purpose`, `patch_path`, and `target_worktree` metadata when producing code-modifying output
- If the maintenance worker does not produce a patch artifact in the expected format, the orchestrator must treat the output as findings-only and record the format mismatch in `key_learnings`

Patch artifact contract:
- code-modifying maintenance and materialization workers must emit one unified diff patch artifact
- each unified diff patch artifact must record `producer_slot_id`, `purpose`, `patch_path`, and `target_worktree`

Compatibility rule:
- only bypass `repo-audit-refactor-optimize` when the worker task is explicitly incompatible with that skill or with the repository state
- when bypassing, fall back to findings-only maintenance and record the incompatibility reason in output and state

## Synthesis Lane

**Skill:** `metaopt-experiment-selection`

Purpose:
- rank eligible proposals and choose one winning proposal

Output:
- exactly one winning proposal
- short ranking rationale

## Design Lane

**Skill:** `metaopt-experiment-design`

Purpose:
- transform the winning proposal into an experiment batch design suitable for the backend contract

Output:
- concrete experiment specification
- execution assumptions
- artifact expectations

## Materialization Lane

**Skill:** `metaopt-experiment-materialization`

Purpose:
- turn the designed experiment into concrete code changes, packageable artifacts, and a manifest-ready local changeset

Output:
- one unified diff patch artifact suitable for mechanical integration
- immutable artifact inputs for the batch manifest
- local verification notes for `LOCAL_SANITY`

Modes:
- **standard** (`materialization_mode: "standard"`): implement an experiment design from scratch (dispatched during `MATERIALIZE_CHANGESET`)
- **remediation** (`materialization_mode: "remediation"`): apply diagnosis-guided code fixes to an existing patch (dispatched during `LOCAL_SANITY` after diagnosis)
- **conflict-resolution** (`materialization_mode: "conflict_resolution"`): resolve non-trivial merge conflicts between patches (dispatched when mechanical patch integration fails)

All modes produce the same output shape (unified diff patch artifact + metadata). The orchestrator passes mode-specific context (experiment design for standard, diagnosis guidance for remediation, conflicting patches for conflict-resolution).

## Diagnosis Lane

**Skill:** `metaopt-sanity-diagnosis`

Purpose:
- explain sanity failures, code failures, or remote failure payloads

Output:
- root-cause summary
- concrete fix recommendation or patch plan

## Analysis Lane

**Skill:** `metaopt-results-analysis`

Purpose:
- compare completed batch results against the aggregate baseline and extract learnings

Output:
- improvement or regression judgment
- updated learnings
- proposal invalidations or carry-over candidates

## Rollover Lane

**Skill:** `metaopt-proposal-rollover`

**Dispatch type:** Inline — the orchestrator dispatches this worker synchronously during `ROLL_ITERATION`. Unlike other lanes, rollover does not consume an `active_slots` entry. The subagent returns before the orchestrator advances to `QUIESCE_SLOTS`.

Purpose:
- filter, merge, and discard proposals from `next_proposals` to produce a clean `current_proposals` pool for the next iteration

Inputs:
- `next_proposals` pool
- results analysis output (judgment, learnings, invalidations, carry-over candidates)
- cumulative `key_learnings`
- `completed_experiments` history
- campaign goal context (`objective.metric`, `objective.direction`, `objective.aggregation`)
- `proposal_policy`
- stop conditions progress

Outputs:
- filtered carry-over proposals (moved into `current_proposals`)
- discard reasons for removed proposals
- merge rationale for merged proposals
- pool health flag (`needs_fresh_ideation` when below `current_floor`)
- summary statistics (carried_over, discarded, merged, final_pool_size)

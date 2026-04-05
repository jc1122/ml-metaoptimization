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
- if patch application conflicts or requires a non-trivial merge, dispatch `strong_coder` for conflict resolution instead of merging manually

Compatibility rule:
- only bypass `repo-audit-refactor-optimize` when the worker task is explicitly incompatible with that skill or with the repository state
- when bypassing, fall back to findings-only maintenance and record the incompatibility reason in output and state

## Synthesis Lane

Purpose:
- rank eligible proposals and choose one winning proposal

Output:
- exactly one winning proposal
- short ranking rationale

## Design Lane

Purpose:
- transform the winning proposal into an experiment batch design suitable for the backend contract

Output:
- concrete experiment specification
- execution assumptions
- artifact expectations

## Materialization Lane

Purpose:
- turn the designed experiment into concrete code changes, packageable artifacts, and a manifest-ready local changeset

Output:
- code changes or patch artifacts suitable for mechanical integration
- immutable artifact inputs for the batch manifest
- local verification notes for `LOCAL_SANITY`

## Diagnosis Lane

Purpose:
- explain sanity failures, code failures, or remote failure payloads

Output:
- root-cause summary
- concrete fix recommendation or patch plan

## Analysis Lane

Purpose:
- compare completed batch results against the aggregate baseline and extract learnings

Output:
- improvement or regression judgment
- updated learnings
- proposal invalidations or carry-over candidates

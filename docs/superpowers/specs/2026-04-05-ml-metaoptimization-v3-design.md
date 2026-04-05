# ML Metaoptimization V3 Contract Cleanup Design

## Problem

The current v2 skill contract is strong on happy-path intent but still leaves several runtime-critical details implicit:

- proposal-threshold resume behavior depends on bookkeeping that is not part of the required state contract
- worker patch artifacts and "mechanical integration" are described but not specified
- code and data artifacts are treated inconsistently across the skill, manifest contract, and example campaign
- retry ownership is split ambiguously between orchestrator and backend
- "continuous" execution is described as if the skill schedules itself, even though progress actually depends on reinvocation
- repository validation depends on undocumented runtime prerequisites

These gaps make the skill harder to implement safely and easier to interpret inconsistently across runtimes.

## Goals

- make persisted state sufficient for deterministic resume decisions
- make worker outputs and mechanical integration explicit and testable
- make code and data artifact handling first-class in the contract
- assign retry ownership cleanly between orchestrator and backend
- document reinvocation and validation dependencies explicitly
- strengthen static validation so the public contract is pinned across docs, examples, and fixtures

## Non-Goals

- adding live queue-backend integration tests
- implementing the runtime orchestrator itself in this repository
- changing the core state-machine phases or overall control-plane model

## Proposed Design

### 1. Versioning and Contract Boundary

The contract will move from **v2** to **v3**.

The state-machine phases remain unchanged. The v3 change is a contract-hardening pass that promotes previously implicit runtime behavior into validated public structure. `SKILL.md`, the reference docs, the example campaign, fixtures, and tests will be updated atomically so the repository exposes one coherent version at a time.

### 2. Runtime Semantics

The skill will be described as **continuous across reinvocations**, not as a self-scheduling daemon.

The resume flow remains:

1. the skill persists `.ml-metaopt/state.json`
2. the `AGENTS.md` hook indicates an active task while `status = RUNNING`
3. a host runtime or user-driven invocation re-enters the skill
4. the skill resumes from `machine_state`

The documentation will explicitly state that this repository does not define or guarantee host-side scheduling behavior beyond the hook contract.

### 3. State Contract Cleanup

The v3 state contract will keep the current top-level lifecycle fields and add required structured bookkeeping for ambiguous behavior.

#### Required additions

- `proposal_cycle`
  - `cycle_id`
  - `current_pool_frozen`
  - `ideation_rounds_by_slot`
  - `shortfall_reason`
- richer `active_slots[]`
  - `model_class`
  - `requested_model`
  - `resolved_model`
- richer `local_changeset`
  - `integration_worktree`
  - `patch_artifacts`
  - `apply_results`
  - `verification_notes`
  - `code_artifact_uri`
  - `data_manifest_uri`

#### Rationale

`proposal_cycle.ideation_rounds_by_slot` makes the floor rule resumable and auditable. Explicit model-resolution fields make "record the substitution in state" enforceable. A structured `local_changeset` makes the orchestrator's mechanical role concrete instead of inferred.

### 4. Patch Artifact and Mechanical Integration Contract

Code-modifying workers will return a **unified diff patch artifact** as the canonical mechanical-integration format.

#### Patch artifact rules

- path root: `.ml-metaopt/artifacts/patches/`
- encoding: UTF-8 text
- format: unified diff suitable for mechanical application
- each patch artifact records:
  - producer slot ID
  - purpose
  - patch path
  - target worktree reference when applicable

#### Orchestrator rules

The orchestrator may only:

- apply the patch mechanically
- record whether application succeeded cleanly
- record conflicts or rejection output
- dispatch a coder for conflict resolution when the patch does not apply cleanly

The orchestrator may not hand-edit semantic code during integration.

### 5. Artifact and Manifest Contract

The artifact surface will be expanded so code and data are represented consistently.

#### Required repository layout

```text
.ml-metaopt/
  state.json
  artifacts/
    code/
    data/
    manifests/
    patches/
```

#### Manifest changes

The batch manifest will require both:

- `artifacts.code_artifact.uri`
- `artifacts.data_manifest.uri`

The data manifest is the immutable reference that ties dataset IDs, source materialization details, and fingerprints to the batch. This makes the skill's `artifacts.data_roots` input visible in the packaged execution contract instead of remaining only implied by campaign config.

### 6. Retry Ownership

Retry ownership will be unified under the queue/backend surface.

The orchestrator will declare retry policy in the campaign and manifest, and the backend will execute that policy. The orchestrator will not also implement competing batch-retry logic in the state machine.

Concretely, retry configuration will move out of the `execution` section and into the `remote_queue` surface as backend-facing policy. The backend contract will describe that it must honor the declared retry policy, and inability to do so is a contract failure before enqueue.

### 7. Dependencies and Runtime Notes

The dependency docs and README will explicitly require:

- a Copilot-style runtime with subagent dispatch
- `git` with worktree support
- mechanical patch application capability compatible with unified diff artifacts
- PyYAML for the validation suite
- a host reinvocation mechanism compatible with the `AGENTS.md` hook contract

These items are already assumed by the current design; the change is to document them as first-class dependencies.

### 8. Validation Changes

The test suite will remain static and contract-focused, but it will become stricter.

#### Planned test updates

- bump the example campaign and state fixtures from v2 to v3
- validate that `proposal_cycle` is required and structurally complete
- validate that slot records include model-resolution fields
- validate that `local_changeset` includes patch/data artifact references
- validate that manifest and backend docs agree on retry ownership
- extend cross-document assertions so `SKILL.md`, references, fixtures, and examples all describe the same required shapes

#### Explicit non-goal

The repository will not try to simulate a live Copilot runtime or real remote backend execution. This repo exists to pin the public contract, not to fake the full host runtime.

## File-Level Change Plan

- `SKILL.md`
  - bump public contract version references to v3
  - clarify reinvocation semantics
  - update required file tree and worker-output expectations
- `references/contracts.md`
  - define v3 campaign, state, slot, changeset, and manifest shapes
- `references/state-machine.md`
  - bind the floor rule to `proposal_cycle`
  - define quiesce cancellation recording against the new state shape
- `references/worker-lanes.md`
  - define patch-artifact expectations and mechanical integration boundaries
- `references/backend-contract.md`
  - clarify retry ownership and required artifact references
- `references/dependencies.md`
  - document explicit runtime and validation prerequisites
- `ml_metaopt_campaign.example.yaml`
  - update to v3 shape, including backend-owned retry policy placement
- `tests/fixtures/**`
  - replace v2 fixtures with v3 fixtures
- `tests/test_metaopt_validation.py`
  - add assertions for the new required structures and cross-document invariants
- `README.md`
  - document validation prerequisites and explain the repo's contract-only scope

## Risks and Mitigations

- **Breaking version migration risk:** The v3 bump makes the break explicit instead of silently shifting behavior inside v2.
- **Over-specification risk:** The design only hardens fields needed to remove observed ambiguity; it does not add live-runtime behavior this repository cannot validate.
- **Patch-format rigidity risk:** Unified diff is intentionally chosen because it is portable, reviewable, and mechanically applicable with standard tooling.

## Success Criteria

The design is successful when:

1. the contract documents no longer rely on hidden runtime bookkeeping for resume behavior
2. worker patch integration is explicit enough to validate statically
3. code/data artifact handling is consistent across the skill, references, example campaign, and fixtures
4. retry ownership is unambiguous
5. the validation suite fails when any of those guarantees drift

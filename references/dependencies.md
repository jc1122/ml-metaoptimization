# Dependencies

## Hard Runtime Dependencies

- GitHub Copilot agent runtime with subagent dispatch
- `git` with worktree support
- unified-diff-compatible mechanical patch application capability
- host reinvocation mechanism compatible with the `AGENTS.md` resume hook
- target repository files:
  - `ml_metaopt_campaign.yaml`
  - `AGENTS.md`
  - `.ml-metaopt/state.json` (created on first run if absent, then reused for resume)
- skill repo assets:
  - `SKILL.md`
  - `references/contracts.md`
  - `references/state-machine.md`
  - `references/worker-lanes.md`
  - `references/dispatch-guide.md`
  - `references/backend-contract.md`
  - `ml_metaopt_campaign.example.yaml`
- PyYAML for the validation suite

## Queue Backend Dependency

The skill requires a queue backend compatible with `references/backend-contract.md`.

Current implementation:
- `ray-hetzner`

The backend must expose enqueue, status, and results commands and must accept immutable batch manifests.

## Campaign-Provided Dependencies

These come from `ml_metaopt_campaign.yaml`:
- objective metric and direction
- aggregation rule
- datasets with stable IDs and fingerprints
- local sanity command
- artifact roots and exclusions
- queue backend commands
- remote execution entrypoint

If any required campaign field is missing or invalid, transition to `BLOCKED_CONFIG`.

## Worker-Target Dependencies

The orchestrator dispatches these named worker targets during the campaign. Each must be available in the agent runtime.

Required worker targets:
- `metaopt-ideation-worker` — background ideation lane custom agent
- `metaopt-selection-worker` — auxiliary synthesis lane
- `metaopt-design-worker` — auxiliary design lane
- `metaopt-materialization-worker` — auxiliary materialization lane custom agent
- `metaopt-diagnosis-worker` — auxiliary diagnosis lane custom agent
- `metaopt-analysis-worker` — auxiliary analysis lane custom agent
- `metaopt-rollover-worker` — inline rollover during `ROLL_ITERATION`
- `repo-audit-refactor-optimize` — background maintenance lane (required by default; may fall back to findings-only if incompatible)

When a required worker target is unavailable, see the Skill Availability section in `SKILL.md` for degradation behavior.

## Optional Enhancement Dependencies

- additional audit or optimization skills invoked indirectly by `repo-audit-refactor-optimize`
- backend-specific tooling required by the selected queue backend

Missing optional dependencies may degrade worker quality, but they do not block the state machine unless a required lane becomes impossible to execute safely.

## Failure Behavior

- Missing hard runtime dependency: stop and surface a terminal error
- Missing campaign-provided dependency: `BLOCKED_CONFIG`
- Missing required backend capability: stop before `ENQUEUE_REMOTE_BATCH`
- Missing required maintenance-worker subskill: treat the maintenance lane as incompatible, fall back to findings-only maintenance, and record the reason explicitly before continuing

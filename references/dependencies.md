# Dependencies

## Environment Dependencies

The following must be available in the execution environment before the campaign can start.

### SkyPilot

SkyPilot must be installed and configured for Vast.ai:
- `sky check` must pass for the Vast.ai provider
- SkyPilot version must support `--idle-minutes-to-autostop`
- The `sky launch` and `sky down` commands must be available on the PATH

### WandB API Key

WandB access must be configured:
- Either `wandb login` has been run interactively, or
- `WANDB_API_KEY` environment variable is set
- The API key must have permissions to create sweeps and manage runs in the configured `wandb.entity` and `wandb.project`

### `skypilot-wandb-worker` Agent

The `skypilot-wandb-worker` custom agent must be available in the runtime. Verified during `HYDRATE_STATE`. If missing, transition to `BLOCKED_CONFIG` with `next_action = "install missing agent: skypilot-wandb-worker"`.

### Project Repository Access

- `project.repo` must be accessible (SSH key or HTTPS credentials for the git URL)
- The repository must contain a training entrypoint that reads hyperparameters from `wandb.config` and logs metrics via `wandb.log(...)`

### Smoke Test Command

- `project.smoke_test_command` must be a valid shell command that exists in the repo root
- It must be runnable locally and complete (or crash) within 60 seconds

### Additional Runtime Dependencies

- GitHub Copilot agent runtime with subagent dispatch
- `git` available on the PATH
- Host reinvocation mechanism compatible with the `AGENTS.md` resume hook
- Target repository files:
  - `ml_metaopt_campaign.yaml`
  - `AGENTS.md`
  - `.ml-metaopt/preflight-readiness.json` (emitted by `metaopt-preflight`; prerequisite before `LOAD_CAMPAIGN` proceeds)
  - `.ml-metaopt/state.json` (created on first run if absent, then reused for resume)
- Skill repo assets:
  - `SKILL.md`
  - `references/contracts.md`
  - `references/state-machine.md`
  - `references/worker-lanes.md`
  - `references/dispatch-guide.md`
  - `references/backend-contract.md`
  - `references/control-protocol.md`
  - `references/dependencies.md`

## Campaign YAML Validation Rules

Enforced by `metaopt-load-campaign` during `LOAD_CAMPAIGN`. Any violation transitions to `BLOCKED_CONFIG`.

### `compute` section

| Field | Constraint |
|-------|------------|
| `compute.provider` | Must be `vast_ai` |
| `compute.accelerator` | Non-empty string (e.g. `A100:1`) |
| `compute.num_sweep_agents` | Integer, `1 <= value <= 16` |
| `compute.idle_timeout_minutes` | Integer, `5 <= value <= 60` |
| `compute.max_budget_usd` | Float, `0 < value <= 100` (hard ceiling; larger values require manual override) |

### `objective` section

| Field | Constraint |
|-------|------------|
| `objective.metric` | Non-empty string |
| `objective.direction` | Must be `maximize` or `minimize` |
| `objective.improvement_threshold` | Float, `> 0` |

### `wandb` section

| Field | Constraint |
|-------|------------|
| `wandb.entity` | Non-empty string, no whitespace |
| `wandb.project` | Non-empty string, no whitespace |

### `proposal_policy` section

| Field | Constraint |
|-------|------------|
| `proposal_policy.current_target` | Integer, `>= 1` |

### `stop_conditions` section

| Field | Constraint |
|-------|------------|
| `stop_conditions.max_iterations` | Integer, `>= 1` |
| `stop_conditions.target_metric` | Float |
| `stop_conditions.max_no_improve_iterations` | Integer, `>= 1` |

### `project` section

| Field | Constraint |
|-------|------------|
| `project.repo` | Non-empty string, valid git URL |
| `project.smoke_test_command` | Non-empty string, valid shell command |

### `campaign` section

| Field | Constraint |
|-------|------------|
| `campaign.name` | Non-empty string |
| `campaign.description` | Non-empty string |

## Worker-Target Dependencies

Required worker targets (block on missing):
- `metaopt-ideation-worker` — background ideation lane
- `metaopt-analysis-worker` — analysis lane
- `skypilot-wandb-worker` — execution lane (directive-dispatched)

These are verified during `HYDRATE_STATE`. If any is missing, transition to `BLOCKED_CONFIG`.

## Failure Behavior

- Missing environment dependency (SkyPilot, WandB key): `BLOCKED_CONFIG`
- Missing campaign field or invalid value: `BLOCKED_CONFIG`
- Missing required worker target: `BLOCKED_CONFIG`
- Identity hash mismatch on resume: `BLOCKED_CONFIG`
- Protocol violation during execution: `BLOCKED_PROTOCOL`

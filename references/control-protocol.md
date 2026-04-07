# Control Protocol

This document is the authoritative reference for the control-handoff protocol used between control agents and the orchestrator.

## Architecture

The orchestrator is a **transport and runtime shell**. It owns file I/O, process lifecycle, subagent dispatch, and state persistence — but it does not make semantic decisions about experiment selection, diagnosis routing, or iteration flow.

**Control agents** are the canonical semantic layer. Each control agent is responsible for planning what work should happen next and gating whether completed work meets transition criteria. The orchestrator executes their directives mechanically.

### Plan / Gate Pattern

Most control agents operate in a two-phase pattern:

1. **Plan phase** — the control agent reads current state, decides what should happen next, and emits a handoff with `launch_requests` and `executor_directives`. The orchestrator executes these directives (launches workers, runs commands, writes files).
2. **Gate phase** — the control agent reads the results of the executed work, decides whether the transition criteria are met, and emits a handoff with `recommended_next_machine_state` and `state_patch`. The orchestrator applies the patch and transitions.

Some control agents (e.g. `metaopt-load-campaign`) operate in a single phase when no executor work is needed.

## Universal Control-Handoff Envelope

Every control agent emits a JSON handoff object conforming to this envelope. Fields marked **required** must always be present; fields marked **optional** may be omitted or null when not applicable.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `handoff_type` | string | yes | Identifies the handoff variant. Format: `<control_agent_short_name>.<phase>`, e.g. `"load_campaign.validate"`, `"background_control.plan"`, `"background_control.gate"`. |
| `control_agent` | string | yes | The producing control agent name, e.g. `"metaopt-background-control"`. Equivalent to the legacy `producer` field. |
| `recommended_next_machine_state` | string or null | yes | The machine state the orchestrator should transition to after applying this handoff. Null when the control agent defers the decision to a later gate phase. |
| `launch_requests` | array | yes | Ordered list of worker launch requests for the orchestrator to execute. Empty array when no launches are needed. Each entry specifies `worker_ref`, `model_class`, `task_file`, and `result_file`. |
| `state_patch` | object or null | yes | A partial state object whose keys the orchestrator merges into `.ml-metaopt/state.json`. Null when no state mutation is needed. Only keys owned by this control agent may appear. |
| `executor_directives` | array or null | yes | Ordered list of instructions for the orchestrator executor phase (e.g. commands to run, files to write, worktrees to create). Empty array or null when no executor action is needed. |
| `summary` | string | yes | Human-readable summary of the handoff decision for logging and debugging. |
| `warnings` | array of strings | yes | Diagnostic warnings that do not block progress but should be logged. Empty array when none. |

### Legacy Compatibility

Existing handoff scripts use `producer` instead of `control_agent` and `phase`/`outcome` instead of `handoff_type`. The canonical field names above are the target schema. During migration, both forms are accepted — the orchestrator treats `producer` as equivalent to `control_agent` and constructs `handoff_type` from `producer` + `phase` when the new field is absent.

## Control Agents

The following control agents form the semantic layer of the metaoptimization state machine:

### `metaopt-load-campaign`

- **Scope:** `LOAD_CAMPAIGN` state
- **Phases:** single-phase (validate)
- **Responsibility:** Validate campaign YAML, compute identity/runtime hashes, detect sentinel values
- **Handoff script:** `scripts/load_campaign_handoff.py`

### `metaopt-hydrate-state`

- **Scope:** `HYDRATE_STATE` state
- **Phases:** single-phase (hydrate)
- **Responsibility:** Initialize or resume state, verify worker-target availability, manage AGENTS.md hook
- **Handoff script:** `scripts/hydrate_state_handoff.py`

### `metaopt-background-control`

- **Scope:** `MAINTAIN_BACKGROUND_POOL`, `WAIT_FOR_PROPOSAL_THRESHOLD` states
- **Phases:** plan (`plan_background_work`) → gate (`gate_background_work`)
- **Responsibility:** Manage background slot allocation, ideation/maintenance mode switching, proposal threshold evaluation
- **Handoff script:** `scripts/background_control_handoff.py`

### `metaopt-select-design`

- **Scope:** `SELECT_EXPERIMENT`, `DESIGN_EXPERIMENT` states
- **Phases:** plan select (`plan_select_experiment`) → gate select + plan design (`gate_select_and_plan_design`) → gate design (`finalize_select_design`)
- **Responsibility:** Freeze proposal pool, orchestrate selection and design workers, persist winning proposal and experiment design
- **Handoff script:** `scripts/select_and_design_handoff.py`

### `metaopt-local-execution-control`

- **Scope:** `MATERIALIZE_CHANGESET`, `LOCAL_SANITY` states
- **Phases:** plan (`plan_local_changeset`) → gate (`gate_local_sanity`)
- **Responsibility:** Plan materialization work, route diagnosis actions, enforce sanity attempt cap
- **Handoff script:** `scripts/local_execution_control_handoff.py`

### `metaopt-remote-execution-control`

- **Scope:** `ENQUEUE_REMOTE_BATCH`, `WAIT_FOR_REMOTE_BATCH`, `ANALYZE_RESULTS` states
- **Phases:** plan enqueue (`plan_remote_batch`) → gate batch status (`gate_remote_batch`) → gate analysis (`analyze_remote_results`)
- **Responsibility:** Generate batch manifests, monitor batch lifecycle, delegate result analysis, update baseline
- **Handoff script:** `scripts/remote_execution_control_handoff.py`

### `metaopt-iteration-close-control`

- **Scope:** `ROLL_ITERATION`, `QUIESCE_SLOTS` states
- **Phases:** plan rollover (`plan_roll_iteration`) → gate rollover (`gate_roll_iteration`) → gate quiesce (`quiesce_slots`)
- **Responsibility:** Orchestrate rollover filtering, emit iteration reports, evaluate stop conditions, drain active slots
- **Handoff script:** `scripts/iteration_close_control_handoff.py`

## State-Patch Ownership

Each control agent owns a defined set of state keys. Only the owning control agent may include these keys in its `state_patch`. The orchestrator must reject patches that write keys outside the agent's ownership scope.

| Control Agent | Owned State Keys |
|---------------|-----------------|
| `metaopt-load-campaign` | `campaign_identity_hash`, `runtime_config_hash`, `objective_snapshot` |
| `metaopt-hydrate-state` | `version`, `campaign_id`, `status` (initialization only), `current_iteration`, `baseline`, `runtime_capabilities`, `proposal_cycle` (initialization only), `active_slots` (initialization only), `current_proposals` (initialization only), `next_proposals` (initialization only), `completed_experiments` (initialization only), `key_learnings` (initialization only), `no_improve_iterations` (initialization only) |
| `metaopt-background-control` | `active_slots` (background class), `proposal_cycle`, `current_proposals` (append only), `next_proposals` (append only) |
| `metaopt-select-design` | `selected_experiment`, `proposal_cycle.current_pool_frozen` |
| `metaopt-local-execution-control` | `local_changeset`, `selected_experiment.sanity_attempts`, `selected_experiment.diagnosis_history` |
| `metaopt-remote-execution-control` | `remote_batches`, `selected_experiment.analysis_summary`, `baseline` (post-analysis update), `no_improve_iterations` |
| `metaopt-iteration-close-control` | `current_iteration`, `status` (lifecycle transitions, e.g. stop-condition → `"completed"`), `current_proposals` (rollover reset), `next_proposals` (rollover drain), `selected_experiment` (clear to null), `local_changeset` (clear to null), `completed_experiments` (append), `key_learnings` (append), `active_slots` (drain/cancel) |

### Orchestrator-Managed Keys

`machine_state` is **not** a `state_patch` key. It is exclusively set by the orchestrator from the envelope's `recommended_next_machine_state` field (see Orchestrator Responsibilities, step 5). The orchestrator validates that the transition is legal per `references/state-machine.md`. Control agents influence `machine_state` only by setting `recommended_next_machine_state` in their handoff envelope.

### Shared Keys

The following keys are written by multiple control agents under strict ordering rules:

- `status`: initialized by `metaopt-hydrate-state` during bootstrap; updated by `metaopt-iteration-close-control` for lifecycle transitions (e.g. setting `"completed"` when stop conditions are met). No other control agent writes `status`.
- `next_action`: exempt from single-owner rule. Every control agent writes this key in its `state_patch` to describe what the orchestrator should do next.
- `active_slots`: background-class slots are owned by `metaopt-background-control`; auxiliary-class slots are owned by the control agent that requested the launch. `metaopt-iteration-close-control` may drain or cancel any slot during `QUIESCE_SLOTS`.

## Orchestrator Responsibilities

The orchestrator is a mechanical executor. Given a control-handoff envelope, it:

1. Validates the envelope structure
2. Applies `state_patch` to `.ml-metaopt/state.json` (rejecting unauthorized keys; `machine_state` is never a valid `state_patch` key)
3. Executes `executor_directives` (file writes, worktree operations, command execution)
4. Launches workers from `launch_requests`
5. Sets `machine_state` to `recommended_next_machine_state` (validating that the transition is legal per `references/state-machine.md`)
6. Persists updated state

The orchestrator never interprets semantic content (e.g., proposal quality, diagnosis routing, stop condition evaluation). These decisions belong exclusively to control agents.

# Control Protocol

This document is the authoritative reference for the control-handoff protocol used between control agents and the orchestrator.

## Architecture

The orchestrator is a **transport and runtime shell**. It owns file I/O, process lifecycle, subagent dispatch, and state persistence — but it does not make semantic decisions about experiment selection, diagnosis routing, or iteration flow.

**Control agents** are the canonical semantic layer. Each control agent is responsible for planning what work should happen next and gating whether completed work meets transition criteria. The orchestrator executes their directives mechanically.

### Plan / Gate Pattern

Most control agents operate in a two-phase pattern:

1. **Plan phase** — the control agent reads current state, decides what should happen next, and emits a handoff with `launch_requests` and `executor_directives`. The orchestrator executes these directives (launches workers, runs commands, writes files). The plan phase sets `recommended_next_machine_state = null` to signal that a gate phase is pending.
2. **Gate phase** — the control agent reads the results of the executed work, decides whether the transition criteria are met, and emits a handoff with `recommended_next_machine_state` and `state_patch`. The orchestrator applies the patch and transitions.

Some control agents (e.g. `metaopt-load-campaign`) operate in a single phase when no executor work is needed.

**Phase selection rule** (how the orchestrator determines which phase to invoke): read the latest handoff file for the current machine state. If it has `recommended_next_machine_state = null`, the plan phase already ran and the gate phase is pending — invoke gate. If no handoff file exists for the current state, or the prior handoff has a non-null `recommended_next_machine_state`, invoke plan. The orchestrator must not infer the phase from any other signal.

## Universal Control-Handoff Envelope

Every control agent emits a JSON handoff object conforming to this envelope. Fields marked **required** must always be present; fields marked **optional** may be omitted or null when not applicable.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `handoff_type` | string | yes | Identifies the handoff variant. Format: `<control_agent_short_name>.<phase_or_mode>`, e.g. `"load_campaign.validate"`, `"background_control.plan_background_work"`, `"background_control.gate_background_work"`. |
| `control_agent` | string | yes | The producing control agent name, e.g. `"metaopt-background-control"`. |
| `recommended_next_machine_state` | string or null | yes | The machine state the orchestrator should transition to after applying this handoff. Null when the control agent defers the decision to a later gate phase. |
| `recovery_action` | string or null | no | Operator-facing recovery guidance for runtime-error or blocked handoffs. This field is descriptive only and must never be executed mechanically. |
| `launch_requests` | array | yes | Ordered list of worker launch requests for the orchestrator to execute. Empty array when no launches are needed. Each entry specifies `worker_ref`, `model_class`, `task_file`, `result_file`, and optionally `preferred_model`. |
| `state_patch` | object or null | yes | A partial state object whose keys the orchestrator merges into `.ml-metaopt/state.json`. Emit an object when the handoff mutates semantic state and `null` when it does not. `machine_state` and `status` are never valid `state_patch` keys. Only keys owned by this control agent may appear. |
| `executor_directives` | array | yes | Ordered list of instructions for the orchestrator executor phase (e.g. commands to run, files to write, worktrees to create). Empty array when no executor action is needed. |
| `summary` | string | yes | Human-readable summary of the handoff decision for logging and debugging. |
| `warnings` | array of strings | yes | Diagnostic warnings that do not block progress but should be logged. Empty array when none. |

### Executor Directive Rules

- `executor_directives` is the authoritative description of executor-side work.
- When a phase requires executor activity, the governing control agent must emit explicit directive objects instead of relying on prose in `summary`, `next_action`, or the state-machine narrative.
- The orchestrator must execute `executor_directives` mechanically in order and must not infer missing executor work from free-form text.
- `summary`, `warnings`, `recovery_action`, and `next_action` are descriptive only. They are never executable instructions.
- Each directive object must contain:
  - `action` — required non-empty string
  - `reason` — required non-empty string explaining why the directive exists
  - action-specific fields documented below
- Phases that have no executor-side work must still emit `executor_directives = []`.

### Executor Directive Catalog

#### Remote execution directives

- `write_manifest` — required fields: `manifest_path`, `batch_id`
- `queue_op` — required fields: `operation` (one of `enqueue`, `status`, `results`), `batch_id`, `command` (full shell command string from campaign `backend` contract), `result_file` (path the orchestrator writes the worker JSON result to, e.g. `.ml-metaopt/queue-results/<op>-<batch_id>.json`). The orchestrator executes this directive by dispatching `@hetzner-delegation-worker` and writing its JSON output to `result_file`. `metaopt-remote-execution-control` reads `result_file` in the subsequent gate or analyze phase.

#### Local execution directives

- `apply_patch_artifacts` — required fields: `result_file`, `target_worktree`
- `package_code_artifact` — required fields: `worktree`, `code_roots`, `output_event_path` (path where the orchestrator writes the resulting artifact URI as an executor event for the control agent to read in gate phase)
- `package_data_manifest` — required fields: `worktree`, `data_roots`, `output_event_path` (same pattern as `package_code_artifact`)
- `run_sanity` — required fields: `worktree`, `command`, `max_duration_seconds`

#### Iteration-close and terminal directives

- `emit_iteration_report` — required fields: `report_type`, `iteration`
- `drain_slots` — required fields: `drain_window_seconds`
- `cancel_slots` — required fields: `slot_ids`
- `remove_agents_hook` — required fields: `agents_path`
- `delete_state_file` — required fields: `state_path`
- `emit_final_report` — required fields: `report_type`

### Launch Request Fields

Each entry in `launch_requests` specifies:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `worker_ref` | string | yes | Worker target name (e.g. `"metaopt-analysis-worker"`) |
| `model_class` | string | yes | Model class (`"strong_coder"`, `"strong_reasoner"`, or `"general_worker"`) |
| `task_file` | string | yes | Path to the staged task file to pass to the worker |
| `result_file` | string | yes | Path where the worker writes its structured result |
| `preferred_model` | string | no | Deterministic model hint. When present, the orchestrator should use this specific model for the launch. Added automatically by `normalize_launch_requests()` when absent: `claude-opus-4.6` (or highest available opus ≥ 4.6) for `strong_reasoner` and `strong_coder`; `claude-sonnet-4` for `general_worker`. If the preferred model is unavailable, take the next fallback — `gpt-5.4` or the highest available gpt ≥ 5.4 — and record the substitution. |
| `slot_class` | string | no | Slot class for slot-based dispatch (`"background"` or `"auxiliary"`) |
| `mode` | string | no | Slot mode for slot-based dispatch |

### Fail-Closed Rule — `BLOCKED_PROTOCOL`

When a control agent encounters unsupported semantic work, lane drift, missing worker artifacts, or any protocol violation it cannot resolve, it must fail closed to `BLOCKED_PROTOCOL` rather than improvising or allowing the orchestrator to attempt generic semantic fallback. The orchestrator is mechanical — it has no ability to perform semantic work — so any attempt to work around a protocol gap would produce undefined behavior.

The orchestrator must never hand-edit semantic state. It only applies control-agent `state_patch` updates, executes `executor_directives`, sets `machine_state` from `recommended_next_machine_state`, and derives `status` from the resulting machine state. Manual state edits to fields such as `baseline`, `selected_experiment`, `completed_experiments`, `key_learnings`, `status`, or `next_action` are protocol violations, even when they appear equivalent to the intended outcome.

Control agents that may emit `BLOCKED_PROTOCOL`:
- `metaopt-hydrate-state`: prior state has an unrecoverable protocol violation
- `metaopt-background-control`: ideation result contains semantic-lane fields (lane drift)
- `metaopt-select-design`: design result contains materialization-lane fields (lane drift)
- `metaopt-local-execution-control`: remediation requested but diagnosis artifact is missing
- `metaopt-remote-execution-control`: result judgment requested but analysis artifact is missing
- `metaopt-iteration-close-control`: rollover output violates contract shape

When emitting `BLOCKED_PROTOCOL`, the control agent must:
1. Set `recommended_next_machine_state` to `"BLOCKED_PROTOCOL"`
2. Include a descriptive `summary` explaining the violation
3. Include `warnings` listing the specific artifacts or fields that triggered the block
4. Set `next_action` in the `state_patch` to describe recovery steps

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
- **Phases:** plan (`plan_local_changeset`) → gate integration (`gate_materialization`) → gate sanity (`gate_local_sanity`)
- **Responsibility:** Plan materialization work, gate integration outcome and emit conflict-resolution `launch_requests` when needed, route diagnosis actions, enforce sanity attempt cap
- **Handoff script:** `scripts/local_execution_control_handoff.py`

### `metaopt-remote-execution-control`

- **Scope:** `ENQUEUE_REMOTE_BATCH`, `WAIT_FOR_REMOTE_BATCH`, `ANALYZE_RESULTS` states
- **Phases:** plan enqueue (`plan_remote_batch`) → gate batch status (`gate_remote_batch`) → gate analysis (`analyze_remote_results`)
- **Responsibility:** Generate batch manifests, emit `queue_op` directives for the orchestrator to dispatch via `@hetzner-delegation-worker`, monitor batch lifecycle, delegate result analysis, update baseline
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
| `metaopt-load-campaign` | *(none — always emits `state_patch: null`; campaign identity and runtime hashes are read from the handoff payload by `metaopt-hydrate-state`)* |
| `metaopt-hydrate-state` | `version`, `campaign_id`, `campaign_identity_hash`, `runtime_config_hash`, `current_iteration`, `next_action`, `objective_snapshot`, `proposal_cycle`, `active_slots`, `current_proposals`, `next_proposals`, `selected_experiment`, `local_changeset`, `remote_batches`, `baseline`, `completed_experiments`, `key_learnings`, `no_improve_iterations`, `maintenance_summary`, `campaign_started_at`, `runtime_capabilities` |
| `metaopt-background-control` | `active_slots` (background class), `proposal_cycle`, `current_proposals` (append only), `next_proposals` (append only), `next_action`, `maintenance_summary` (append only) |
| `metaopt-select-design` | `selected_experiment`, `proposal_cycle.current_pool_frozen`, `next_action` |
| `metaopt-local-execution-control` | `local_changeset`, `selected_experiment.sanity_attempts`, `selected_experiment.diagnosis_history`, `next_action` |
| `metaopt-remote-execution-control` | `pending_remote_batch`, `remote_batches`, `selected_experiment.analysis_summary`, `selected_experiment.diagnosis_history`, `baseline`, `no_improve_iterations`, `completed_experiments`, `key_learnings`, `next_action` |
| `metaopt-iteration-close-control` | `current_iteration`, `current_proposals`, `next_proposals`, `selected_experiment`, `local_changeset`, `completed_experiments`, `key_learnings`, `active_slots`, `last_iteration_report`, `next_action` |

### Orchestrator-Managed Keys

`machine_state` is **not** a `state_patch` key. It is exclusively set by the orchestrator from the envelope's `recommended_next_machine_state` field (see Orchestrator Responsibilities, step 5). The orchestrator validates that the transition is legal per `references/state-machine.md`. Control agents influence `machine_state` only by setting `recommended_next_machine_state` in their handoff envelope.

### Shared Keys

The following keys are written by multiple control agents under strict ordering rules:

- `status`: derived centrally by the orchestrator from `machine_state`. Control agents never write it in `state_patch`.
- `next_action`: exempt from single-owner rule. Control agents may write it in `state_patch` as operator guidance. The orchestrator must never execute from it.
- `active_slots`: background-class slots are owned by `metaopt-background-control`; auxiliary-class slots are owned by the control agent that requested the launch. `metaopt-iteration-close-control` may drain or cancel any slot during `QUIESCE_SLOTS`.
- `local_changeset`: written by `metaopt-local-execution-control` during `MATERIALIZE_CHANGESET` / `LOCAL_SANITY`, then extended or cleared by `metaopt-iteration-close-control` during `QUIESCE_SLOTS`. The ordering rule is strict: iteration-close only touches it after local-execution has finished for that iteration.

## Orchestrator Responsibilities

The orchestrator is a mechanical executor. Given a control-handoff envelope, it:

1. Validates the envelope structure
2. Applies `state_patch` to `.ml-metaopt/state.json` (rejecting unauthorized keys; `machine_state` and `status` are never valid `state_patch` keys)
3. Executes `executor_directives` (file writes, worktree operations, command execution)
4. Launches workers from `launch_requests`
5. Sets `machine_state` to `recommended_next_machine_state` (validating that the transition is legal per `references/state-machine.md`)
6. Derives `status` from `machine_state`
7. Persists updated state

The orchestrator never interprets semantic content (e.g., proposal quality, diagnosis routing, stop condition evaluation). These decisions belong exclusively to control agents.

### Pre-Transition Self-Check

Before executing any state transition, the orchestrator must verify:

- Every semantic decision driving this transition originated from a control-agent handoff envelope — not from orchestrator-local reasoning.
- No field in `state_patch` was computed, inferred, or modified by the orchestrator. The orchestrator applies patches verbatim.
- All `executor_directives` came from the handoff envelope. The orchestrator did not add, remove, or reorder directives based on its own interpretation of `summary`, `next_action`, or prose elsewhere in the document.
- If any of the above conditions is not met, the orchestrator must stop and transition to `BLOCKED_PROTOCOL` with `next_action` describing which decision was taken outside the control-agent boundary.

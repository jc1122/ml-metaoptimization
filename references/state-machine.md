# State Machine

## States

- `LOAD_CAMPAIGN`
- `HYDRATE_STATE`
- `MAINTAIN_BACKGROUND_POOL`
- `WAIT_FOR_PROPOSAL_THRESHOLD`
- `SELECT_EXPERIMENT`
- `DESIGN_EXPERIMENT`
- `MATERIALIZE_CHANGESET`
- `LOCAL_SANITY`
- `ENQUEUE_REMOTE_BATCH`
- `WAIT_FOR_REMOTE_BATCH`
- `ANALYZE_RESULTS`
- `ROLL_ITERATION`
- `QUIESCE_SLOTS`
- `COMPLETE`
- `BLOCKED_CONFIG`
- `BLOCKED_PROTOCOL`
- `FAILED`

## Control-Agent Dispatch Map

Every delegated phase is governed by a mandatory control agent. The orchestrator must invoke the designated control agent before and after each executor phase; it must not make semantic decisions itself. See `references/control-protocol.md` for the handoff envelope schema and `references/dispatch-guide.md` for per-state dispatch details.

| State(s) | Control Agent | Plan Phase | Gate Phase(s) |
|----------|--------------|------------|---------------|
| `LOAD_CAMPAIGN` | `metaopt-load-campaign` | single-phase (validate) | — |
| `HYDRATE_STATE` | `metaopt-hydrate-state` | single-phase (hydrate) | — |
| `MAINTAIN_BACKGROUND_POOL`, `WAIT_FOR_PROPOSAL_THRESHOLD` | `metaopt-background-control` | `plan_background_work` | `gate_background_work` |
| `SELECT_EXPERIMENT`, `DESIGN_EXPERIMENT` | `metaopt-select-design` | `plan_select_experiment` | `gate_select_and_plan_design`, `finalize_select_design` |
| `MATERIALIZE_CHANGESET`, `LOCAL_SANITY` | `metaopt-local-execution-control` | `plan_local_changeset` | `gate_materialization`, `gate_local_sanity` |
| `ENQUEUE_REMOTE_BATCH`, `WAIT_FOR_REMOTE_BATCH`, `ANALYZE_RESULTS` | `metaopt-remote-execution-control` | `plan_remote_batch` | `gate_remote_batch`, `analyze_remote_results` |
| `ROLL_ITERATION`, `QUIESCE_SLOTS` | `metaopt-iteration-close-control` | `plan_roll_iteration` | `gate_roll_iteration`, `quiesce_slots` |

## Event Priority

Within each reinvocation, the orchestrator processes events in this order before invoking the governing control agent:

1. Stage raw outputs of any completed slots
2. Invoke the governing control agent in the correct phase and apply the resulting handoff. Phase selection rule: if the latest handoff file for the current state has `recommended_next_machine_state = null`, the plan phase already ran and the gate phase is pending — invoke gate. If no handoff exists for the current state, or the prior handoff already completed (non-null `recommended_next_machine_state`), invoke plan.
3. During `QUIESCE_SLOTS`, execute only drain and cancel directives from the handoff — do not launch new workers

## Executor Directives

- Whenever executor-side work is required, the governing control agent must emit explicit `executor_directives` in the handoff envelope.
- The orchestrator executes these directives mechanically and must not infer executor work from prose descriptions in this document.
- A phase that has no executor-side work still emits `executor_directives = []` so the absence of work is explicit.

## Transition Semantics

> **Reading these sections:** The bullets below describe the *behaviour* the system must produce in each state. For states labelled "governed by `<control-agent>`", the detailed semantic bullets are the control agent's responsibility — the orchestrator executes them mechanically from the control agent's `executor_directives`, `launch_requests`, and `state_patch`. The orchestrator must not apply these rules autonomously. See `references/control-protocol.md` for the handoff protocol.

### `LOAD_CAMPAIGN`

This state is governed by `metaopt-load-campaign`. The control agent validates the campaign YAML, evaluates the preflight readiness artifact, and emits a single-phase handoff (`validate`). The orchestrator applies the handoff mechanically. See `references/control-protocol.md`.

- Read `ml_metaopt_campaign.yaml`
- Validate required fields and schema shape
- Reject sentinel placeholders such as angle-bracket paths, `YOUR_*`, and dataset fingerprints containing `replace-me`
- Compute `campaign_identity_hash` and `runtime_config_hash` using the canonical rules from `references/contracts.md`
- If validation fails, write `status = BLOCKED_CONFIG`, set `next_action = "repair ml_metaopt_campaign.yaml"`, and stop
- **Preflight gate** (evaluated only when campaign validation passes):
  - Read `.ml-metaopt/preflight-readiness.json` (artifact emitted by `metaopt-preflight`)
  - If the artifact is missing, unreadable, or has an unrecognized `schema_version` → block with `next_action = "run metaopt-preflight"`
  - If the artifact is present but binding freshness fails (hash mismatch on `campaign_identity_hash` or `runtime_config_hash`) → block with `next_action = "re-run metaopt-preflight (campaign configuration has changed)"`
  - If binding freshness passes and `status` is `FAILED` → block using the artifact's `next_action` and `failures` to present actionable remediation (the environment is not ready, not that configuration has changed)
  - If binding freshness passes and `status` is `READY` → proceed to `HYDRATE_STATE`
- The handoff includes a `preflight_readiness` advisory payload describing the observed artifact status for inspectability

### `HYDRATE_STATE`

This state is governed by `metaopt-hydrate-state`. The control agent initializes or resumes state, manages the `AGENTS.md` hook, verifies worker target availability, and emits a single-phase handoff (`hydrate`). The orchestrator applies the `state_patch` and transitions. See `references/control-protocol.md`.

- If `.ml-metaopt/state.json` exists and `campaign_identity_hash` matches the campaign identity, resume from `machine_state`
- If `.ml-metaopt/state.json` exists and there is a campaign identity hash mismatch, transition to `BLOCKED_CONFIG`, preserve the stale state in place, set `next_action = "archive or remove the stale state before starting a new campaign"`, remove the `AGENTS.md` hook, and stop. (This removal calls the same operation as terminal-state cleanup — it strips only the `<!-- ml-metaoptimization:begin -->...<!-- ml-metaoptimization:end -->` block — but here it is a hard stop with no state-machine transition to a final state.)
- Otherwise initialize fresh state from the campaign spec
- If `AGENTS.md` does not exist, create it
- Ensure the marked `AGENTS.md` hook is present only while `status = RUNNING`
- Verify required worker target availability and record the result in `state.runtime_capabilities`; if any required target is missing, transition to `BLOCKED_CONFIG` with `next_action = "install missing skill: <skill_name>"`

### `MAINTAIN_BACKGROUND_POOL`

This state is governed by `metaopt-background-control`. The control agent plans slot launches (via staged task files), the orchestrator dispatches workers mechanically, and the control agent gates completed outputs. See `references/control-protocol.md` for the handoff protocol.

- Ensure exactly `dispatch_policy.background_slots` background slots exist
- Prefer ideation via the `metaopt-ideation-worker` custom agent when `current_proposals` is below target and `next_proposals` is below cap
- Otherwise assign maintenance work via `repo-audit-refactor-optimize`
- **Patch integration timing:** maintenance workers may produce patch outputs, but those patches are NOT applied during background work. When a maintenance slot completes, the orchestrator records the patch path as an executor event in `.ml-metaopt/executor-events/`. Integration is deferred to `QUIESCE_SLOTS`, where `metaopt-iteration-close-control` emits `apply_patch_artifacts` directives and the orchestrator applies them mechanically.
- The current proposal cycle starts on the first entry into this state for an iteration
- Create or reset `proposal_cycle.cycle_id` when a new iteration first enters this state after `ROLL_ITERATION` or fresh initialization
- Set `proposal_cycle.current_pool_frozen = false` when a new proposal cycle begins and keep it false while `current_proposals` may still grow
- Clear `proposal_cycle.shortfall_reason` when a new cycle begins or when the target threshold is later satisfied
- Persist round bookkeeping in `proposal_cycle.ideation_rounds_by_slot`
- Increment `proposal_cycle.ideation_rounds_by_slot[slot_id]` each time a background ideation slot finishes and its output is persisted
- If the machine reaches this state with zero active slots, refill background slots here rather than launching ad hoc workers outside the slot accounting rules

### `WAIT_FOR_PROPOSAL_THRESHOLD`

This state is governed by `metaopt-background-control`. The control agent evaluates proposal readiness against the threshold; the orchestrator must not assess proposal counts independently. See `references/control-protocol.md`.


- Require `proposal_policy.current_target` distinct, non-overlapping proposals in `current_proposals`
- `proposal_cycle` uses persisted `ideation_rounds_by_slot` bookkeeping for the floor rule so reinvocations resume the same round counts instead of restarting them
- Floor rule: if the persisted `proposal_cycle.ideation_rounds_by_slot` bookkeeping shows every background slot has completed two ideation rounds in the current cycle and fewer than the target exist, allow progress once `proposal_policy.current_floor` is reached
- If the floor is still not met, continue background ideation and set `proposal_cycle.shortfall_reason` to the current blocking reason
- Clear `proposal_cycle.shortfall_reason` once progress is allowed into `SELECT_EXPERIMENT`

### `SELECT_EXPERIMENT`

This state is governed by `metaopt-select-design`. The control agent writes a staged selection task, the orchestrator launches `metaopt-selection-worker`, and the control agent validates the winning proposal before advancing. See `references/control-protocol.md`.

- Dispatch the `metaopt-selection-worker` custom agent as one `strong_reasoner` subagent
- Input: `current_proposals`, baseline context, prior learnings, and completed experiments
- Output: exactly one winning proposal and a short ranking rationale
- Freeze `current_proposals` by setting `proposal_cycle.current_pool_frozen = true` once selection starts
- The current proposal cycle ends when this state begins; keep `proposal_cycle.cycle_id` stable for auditability until the next iteration resets it

### `DESIGN_EXPERIMENT`

This state is governed by `metaopt-select-design`. The control agent writes a staged design task, the orchestrator launches `metaopt-design-worker`, and the control agent finalizes `state.selected_experiment.design` before `MATERIALIZE_CHANGESET`. See `references/control-protocol.md`.

- Dispatch the `metaopt-design-worker` custom agent as one `strong_reasoner` subagent
- Input: the winning proposal, baseline context, queue/backend constraints, and prior learnings
- Output: exactly one concrete experiment specification plus execution assumptions and artifact expectations
- Persist the experiment design before any coder starts `MATERIALIZE_CHANGESET`

### `MATERIALIZE_CHANGESET`

This state is governed by `metaopt-local-execution-control`. The control agent writes staged materialization tasks, the orchestrator launches workers and applies patches mechanically, and the control agent gates the results. See `references/control-protocol.md`.

- Dispatch the `metaopt-materialization-worker` custom agent as `strong_coder` subagents in isolated worktrees
- Count these coders against `auxiliary_slots` with `mode = materialization`
- `metaopt-local-execution-control`'s `plan_local_changeset` handoff emits an `apply_patch_artifacts` directive (with `output_event_path` set) ordering the orchestrator to attempt mechanical patch integration and write the outcome — success or conflict details — as an executor event
- The orchestrator then re-invokes `metaopt-local-execution-control` in `gate_materialization` phase; the control agent reads the integration outcome executor event and either emits a conflict-resolution `launch_requests` entry (patches did not merge cleanly) or advances to `LOCAL_SANITY` (merge succeeded)
- Conflict-resolution is still part of the `MATERIALIZE_CHANGESET` state; the machine advances to `LOCAL_SANITY` only after successful integration
- Package an immutable code artifact under `.ml-metaopt/artifacts/code/`
- Package the manifest-linked data artifact inputs under `.ml-metaopt/artifacts/data/`
- Persist one unified diff patch artifact for each code-modifying worker under `.ml-metaopt/artifacts/patches/`
- Write a batch manifest under `.ml-metaopt/artifacts/manifests/`

### `LOCAL_SANITY`

This state is governed by `metaopt-local-execution-control`. The control agent emits `run_sanity` directives; the orchestrator executes them and stages raw outputs; the control agent interprets results and routes retries. See `references/control-protocol.md`.

- `metaopt-local-execution-control` emits a `run_sanity` directive (in the handoff that advances into `LOCAL_SANITY` and after each remediation cycle) with `output_event_path` set; the orchestrator runs `sanity.command` and writes captured stdout, stderr, exit-code, and duration to `output_event_path`
- Semantic interpretation and retry routing are the exclusive responsibility of `metaopt-local-execution-control`; the orchestrator must not evaluate sanity outcomes directly
- Required checks:
  - config loads
  - fast path executes
  - temporal leakage passes when required
- Allow a maximum 3 remediation attempts for the selected experiment
- If sanity fails and `sanity_attempts < 3`:
  - Dispatch the `metaopt-diagnosis-worker` custom agent as a `strong_reasoner` subagent with the failure output, experiment design, patch summary, and prior diagnosis history from `state.selected_experiment.diagnosis_history`
  - Persist the diagnosis record to `state.selected_experiment.diagnosis_history`
  - Increment `state.selected_experiment.sanity_attempts`
  - Route on `fix_recommendation.action`:
    - `"fix"`: dispatch `metaopt-materialization-worker` in remediation mode with `code_guidance` from the diagnosis, the original experiment design, and the current patch state. The materialization worker produces an updated unified diff patch. Rerun `LOCAL_SANITY` after integration.
    - `"adjust_config"`: transition to `BLOCKED_CONFIG` with `next_action` set to the `config_guidance` from the diagnosis. The orchestrator cannot autonomously modify campaign configuration.
    - `"abandon"`: transition to `FAILED` with the diagnosis `root_cause` as the terminal error
- If `sanity_attempts >= 3`, transition to `FAILED` regardless of diagnosis output

### `ENQUEUE_REMOTE_BATCH`

This state is governed by `metaopt-remote-execution-control`. The control agent validates enqueue readiness, writes the manifest (via a `write_manifest` executor directive), and emits a `queue_op` directive for `enqueue`. The orchestrator dispatches `@hetzner-delegation-worker` and writes the result to `.ml-metaopt/queue-results/enqueue-<batch_id>.json`. The control agent reads that file in the gate phase. See `references/control-protocol.md`.

- `metaopt-remote-execution-control` emits a `queue_op` directive; orchestrator dispatches `@hetzner-delegation-worker` with `remote_queue.enqueue_command`
- Pass exactly one immutable batch manifest
- Expect one stdout JSON object containing `batch_id`, `queue_ref`, and `status = "queued"`
- Control agent records `batch_id` and queue reference in `state_patch`; orchestrator applies it

### `WAIT_FOR_REMOTE_BATCH`

This state is governed by `metaopt-remote-execution-control`. The control agent emits `queue_op` directives for status polling; the orchestrator dispatches `@hetzner-delegation-worker` and writes results for the control agent to interpret. See `references/control-protocol.md`.

- Continue background-slot work while the batch runs
- `metaopt-remote-execution-control` emits `queue_op` directives for `remote_queue.status_command`; orchestrator dispatches `@hetzner-delegation-worker` and writes raw backend payloads to `.ml-metaopt/queue-results/status-<batch_id>.json`
- Semantic interpretation and remote failure routing are the exclusive responsibility of `metaopt-remote-execution-control`
- Never inspect raw cluster jobs directly from this skill
- If `stop_conditions.max_wallclock_hours` is exceeded, set `next_action = "finish current batch and stop"`, stop launching new work, and continue polling the current batch to completion
- If all slots are unexpectedly idle during this state, transition through `MAINTAIN_BACKGROUND_POOL` to restore the declared slot set before doing any lower-priority work
- If `status_command` returns `status = "failed"`:
  - Dispatch the `metaopt-diagnosis-worker` custom agent as a `strong_reasoner` subagent with the remote failure context (`classification`, `message`, `returncode` from the backend response)
  - Persist the diagnosis record to `state.selected_experiment.diagnosis_history`
  - Route on `fix_recommendation.action`:
    - `"fix"`: the failure was caused by experiment code — transition to `FAILED` (remote failures cannot be remediated locally without re-enqueueing)
    - `"adjust_config"`: transition to `BLOCKED_CONFIG` with `next_action = <config_guidance>`
    - `"abandon"`: transition to `FAILED` with the diagnosis `root_cause` as the terminal error
  - In all cases, append remote failure learnings to `state.key_learnings` before transitioning
  - Remote retries are the backend's responsibility via `remote_queue.retry_policy`; the orchestrator never re-enqueues a failed batch

### `ANALYZE_RESULTS`

This state is governed by `metaopt-remote-execution-control`. The control agent emits a `queue_op` directive for results fetch; the orchestrator dispatches `@hetzner-delegation-worker` and writes results to `.ml-metaopt/queue-results/results-<batch_id>.json`. The control agent reads that file, stages analysis tasks, and updates baseline state. See `references/control-protocol.md`.

- `metaopt-remote-execution-control` emits a `queue_op` directive; orchestrator dispatches `@hetzner-delegation-worker` with `remote_queue.results_command`
- The control agent reads the result file and stages raw completed-results payloads; semantic result judgment and baseline updates are its exclusive responsibility
- Dispatch the `metaopt-analysis-worker` custom agent as one `strong_reasoner` subagent to compare the result against the aggregate baseline and extract learnings
- If the aggregate result clears `objective.improvement_threshold` in the configured direction, update the baseline and reset `no_improve_iterations` to `0`
- Otherwise leave the baseline unchanged and increment `no_improve_iterations`
- Update completed experiments and learnings in both cases

### `ROLL_ITERATION`

This state is governed by `metaopt-iteration-close-control`. The control agent writes a staged rollover task, the orchestrator launches `metaopt-rollover-worker`, and the control agent integrates rollover output and evaluates stop conditions. See `references/control-protocol.md`.

- Dispatch the `metaopt-rollover-worker` custom agent as one `strong_reasoner` subagent (inline dispatch — no slot consumed)
- Input: `next_proposals`, fresh `key_learnings`, completed experiment results, and updated baseline
- Output: filtered carry-over proposals with duplicates, invalidated ideas, and overlaps removed plus short rationale for each removal
- Move the filtered survivors into `current_proposals`
- Clear `next_proposals`
- Increment iteration counters only when the campaign will continue into another iteration; if a stop condition is already met, keep `current_iteration` equal to the just-completed iteration number
- Clear `selected_experiment` (set to `null`) after persisting the completed experiment record to `completed_experiments`
- Check stop conditions using the aggregate metric
- Stop when any configured stop condition is met: `target_metric`, `max_iterations`, `max_no_improve_iterations`, or `max_wallclock_hours` (elapsed time since `campaign_started_at`)
- Emit the iteration report using the contract in `references/contracts.md`
- Transition to `QUIESCE_SLOTS` regardless of whether the campaign continues or stops

### `QUIESCE_SLOTS`

This state is governed by `metaopt-iteration-close-control`. The orchestrator drains active slots and stages raw outcomes; the control agent decides whether the campaign continues or completes. See `references/control-protocol.md`.

- The orchestrator executes `drain_slots` and `cancel_slots` directives from the `quiesce_slots` handoff; it stages raw outcomes (drain results, cancellation exit codes) as executor events in `.ml-metaopt/executor-events/` — it does not write to `state.json` autonomously
- `metaopt-iteration-close-control` reads those executor events and returns cancellation reasons, `apply_results` updates, and the continue/stop decision in its `state_patch` and `recommended_next_machine_state`
- The orchestrator applies the `state_patch` (which records cancellation reasons and patch-application outcomes into `local_changeset.apply_results`) and transitions per `recommended_next_machine_state`
- Do not launch new work during this state — execute only drain and cancel directives

### Terminal States

- `COMPLETE`: emit the final report using the contract in `references/contracts.md` after all slots have already been drained or canceled, remove the `AGENTS.md` hook, delete `.ml-metaopt/state.json`, and stop
- `BLOCKED_CONFIG`: remove the `AGENTS.md` hook, leave state and artifacts intact so the campaign can resume after config repair, and stop
- `BLOCKED_PROTOCOL`: remove the `AGENTS.md` hook, preserve state and all artifacts so the operator can diagnose and recover, and stop. This state is reached when the orchestrator or a control agent encounters unsupported semantic work that cannot be represented by the protocol. Rather than improvising, the machine fails closed to `BLOCKED_PROTOCOL` with a descriptive `next_action` explaining what went wrong and how to recover.
- `FAILED`: remove the `AGENTS.md` hook, write the terminal error, preserve state, and stop

All four terminal states remove the `AGENTS.md` hook using the same operation as the identity-drift path in `HYDRATE_STATE`: strip only the `<!-- ml-metaoptimization:begin -->...<!-- ml-metaoptimization:end -->` block. The difference is that terminal-state cleanup transitions the machine to a final state (`COMPLETE`, `BLOCKED_CONFIG`, `BLOCKED_PROTOCOL`, or `FAILED`), whereas the identity-drift path is a hard stop that does not advance the state machine.

`BLOCKED_PROTOCOL` vs `BLOCKED_CONFIG`: `BLOCKED_CONFIG` signals that the campaign YAML or environment configuration needs repair (user-actionable). `BLOCKED_PROTOCOL` signals that the orchestrator or a control agent detected a protocol-level violation — such as lane drift, missing worker artifacts, or unsupported semantic operations — that cannot be resolved without manual intervention. The orchestrator must never attempt to improvise around a protocol violation.

Any control agent that recommends a terminal state must emit the appropriate cleanup `executor_directives` in its handoff so the orchestrator never infers cleanup intent from prose. The orchestrator executes these directives mechanically without semantic interpretation.

| Terminal state | Required directives |
|---------------|---------------------|
| `COMPLETE` | `remove_agents_hook`, `delete_state_file`, `emit_final_report` |
| `BLOCKED_CONFIG` | `remove_agents_hook` |
| `BLOCKED_PROTOCOL` | `remove_agents_hook` |
| `FAILED` | `remove_agents_hook` |

The `COMPLETE` terminal state is only reachable via `QUIESCE_SLOTS` (after all slots have been drained or cancelled). The other terminal states may be recommended by any control agent from any state.

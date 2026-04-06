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
- `FAILED`

## Event Priority

1. Persist completed slot output
2. Refill an empty background slot
3. Process remote batch status changes
4. Evaluate transition guards

## Transition Semantics

### `LOAD_CAMPAIGN`

- Read `ml_metaopt_campaign.yaml`
- Validate required fields and schema shape
- Reject sentinel placeholders such as angle-bracket paths, `YOUR_*`, and dataset fingerprints containing `replace-me`
- Compute `campaign_identity_hash` and `runtime_config_hash` using the canonical rules from `references/contracts.md`
- If validation fails, write `status = BLOCKED_CONFIG`, set `next_action = "repair ml_metaopt_campaign.yaml"`, and stop

### `HYDRATE_STATE`

- If `.ml-metaopt/state.json` exists and `campaign_identity_hash` matches the campaign identity, resume from `machine_state`
- If `.ml-metaopt/state.json` exists and there is a campaign identity hash mismatch, transition to `BLOCKED_CONFIG`, preserve the stale state in place, set `next_action = "archive or remove the stale state before starting a new campaign"`, remove the `AGENTS.md` hook, and stop. (This removal calls the same operation as terminal-state cleanup — it strips only the `<!-- ml-metaoptimization:begin -->...<!-- ml-metaoptimization:end -->` block — but here it is a hard stop with no state-machine transition to a final state.)
- Otherwise initialize fresh state from the campaign spec
- If `AGENTS.md` does not exist, create it
- Ensure the marked `AGENTS.md` hook is present only while `status = RUNNING`
- Verify required worker skill availability and record the result in `state.runtime_capabilities`; if any required skill is missing, transition to `BLOCKED_CONFIG` with `next_action = "install missing skill: <skill_name>"`

### `MAINTAIN_BACKGROUND_POOL`

- Ensure exactly `dispatch_policy.background_slots` background slots exist
- Prefer ideation via `metaopt-experiment-ideation` when `current_proposals` is below target and `next_proposals` is below cap
- Otherwise assign maintenance work via `repo-audit-refactor-optimize`
- **Patch integration timing:** maintenance workers may produce patch outputs, but those patches are NOT applied automatically during background work. The orchestrator collects completed maintenance outputs and defers patch application (if any) to `QUIESCE_SLOTS`, where mechanical integration happens before rollover.
- The current proposal cycle starts on the first entry into this state for an iteration
- Create or reset `proposal_cycle.cycle_id` when a new iteration first enters this state after `ROLL_ITERATION` or fresh initialization
- Set `proposal_cycle.current_pool_frozen = false` when a new proposal cycle begins and keep it false while `current_proposals` may still grow
- Clear `proposal_cycle.shortfall_reason` when a new cycle begins or when the target threshold is later satisfied
- Persist round bookkeeping in `proposal_cycle.ideation_rounds_by_slot`
- Increment `proposal_cycle.ideation_rounds_by_slot[slot_id]` each time a background ideation slot finishes and its output is persisted
- If the machine reaches this state with zero active slots, refill background slots here rather than launching ad hoc workers outside the slot accounting rules

### `WAIT_FOR_PROPOSAL_THRESHOLD`

- Require `proposal_policy.current_target` distinct, non-overlapping proposals in `current_proposals`
- `proposal_cycle` uses persisted `ideation_rounds_by_slot` bookkeeping for the floor rule so reinvocations resume the same round counts instead of restarting them
- Floor rule: if the persisted `proposal_cycle.ideation_rounds_by_slot` bookkeeping shows every background slot has completed two ideation rounds in the current cycle and fewer than the target exist, allow progress once `proposal_policy.current_floor` is reached
- If the floor is still not met, continue background ideation and set `proposal_cycle.shortfall_reason` to the current blocking reason
- Clear `proposal_cycle.shortfall_reason` once progress is allowed into `SELECT_EXPERIMENT`

### `SELECT_EXPERIMENT`

- Dispatch `metaopt-experiment-selection` as one `strong_reasoner` subagent
- Input: `current_proposals`, baseline context, prior learnings, and completed experiments
- Output: exactly one winning proposal and a short ranking rationale
- Freeze `current_proposals` by setting `proposal_cycle.current_pool_frozen = true` once selection starts
- The current proposal cycle ends when this state begins; keep `proposal_cycle.cycle_id` stable for auditability until the next iteration resets it

### `DESIGN_EXPERIMENT`

- Dispatch `metaopt-experiment-design` as one `strong_reasoner` subagent
- Input: the winning proposal, baseline context, queue/backend constraints, and prior learnings
- Output: exactly one concrete experiment specification plus execution assumptions and artifact expectations
- Persist the experiment design before any coder starts `MATERIALIZE_CHANGESET`

### `MATERIALIZE_CHANGESET`

- Dispatch `metaopt-experiment-materialization` as `strong_coder` subagents in isolated worktrees
- Count these coders against `auxiliary_slots` with `mode = materialization`
- The orchestrator performs clean, mechanical integration (clean merges only) immediately after the materialization subagent finishes
- If mechanical integration fails due to conflicts (i.e. patches do not merge cleanly), the orchestrator dispatches `metaopt-experiment-materialization` in `conflict_resolution` mode to resolve them. This conflict-resolution dispatch is still part of the `MATERIALIZE_CHANGESET` state; the machine advances to `LOCAL_SANITY` only after successful integration.
- Package an immutable code artifact under `.ml-metaopt/artifacts/code/`
- Package the manifest-linked data artifact inputs under `.ml-metaopt/artifacts/data/`
- Persist one unified diff patch artifact for each code-modifying worker under `.ml-metaopt/artifacts/patches/`
- Write a batch manifest under `.ml-metaopt/artifacts/manifests/`

### `LOCAL_SANITY`

- Run `sanity.command`
- Enforce `sanity.max_duration_seconds`
- Required checks:
  - config loads
  - fast path executes
  - temporal leakage passes when required
- Allow a maximum 3 remediation attempts for the selected experiment
- If sanity fails and `sanity_attempts < 3`:
  - Dispatch `metaopt-sanity-diagnosis` as a `strong_reasoner` subagent with the failure output, experiment design, patch summary, and prior diagnosis history from `state.selected_experiment.diagnosis_history`
  - Persist the diagnosis record to `state.selected_experiment.diagnosis_history`
  - Increment `state.selected_experiment.sanity_attempts`
  - Route on `fix_recommendation.action`:
    - `"fix"`: dispatch `metaopt-experiment-materialization` in remediation mode with `code_guidance` from the diagnosis, the original experiment design, and the current patch state. The materialization worker produces an updated unified diff patch. Rerun `LOCAL_SANITY` after integration.
    - `"adjust_config"`: transition to `BLOCKED_CONFIG` with `next_action` set to the `config_guidance` from the diagnosis. The orchestrator cannot autonomously modify campaign configuration.
    - `"abandon"`: transition to `FAILED` with the diagnosis `root_cause` as the terminal error
- If `sanity_attempts >= 3`, transition to `FAILED` regardless of diagnosis output

### `ENQUEUE_REMOTE_BATCH`

- Call `remote_queue.enqueue_command`
- Pass exactly one immutable batch manifest
- Expect one stdout JSON object containing `batch_id`, `queue_ref`, and `status = "queued"`
- Record `batch_id` and queue reference in state

### `WAIT_FOR_REMOTE_BATCH`

- Continue background-slot work while the batch runs
- Poll only `remote_queue.status_command`
- Never inspect raw cluster jobs directly from this skill
- If `stop_conditions.max_wallclock_hours` is exceeded, set `next_action = "finish current batch and stop"`, stop launching new work, and continue polling the current batch to completion
- If all slots are unexpectedly idle during this state, transition through `MAINTAIN_BACKGROUND_POOL` to restore the declared slot set before doing any lower-priority work
- If `status_command` returns `status = "failed"`:
  - Dispatch `metaopt-sanity-diagnosis` as a `strong_reasoner` subagent with the remote failure context (`classification`, `message`, `returncode` from the backend response)
  - Persist the diagnosis record to `state.selected_experiment.diagnosis_history`
  - Route on `fix_recommendation.action`:
    - `"fix"`: the failure was caused by experiment code — transition to `FAILED` (remote failures cannot be remediated locally without re-enqueueing)
    - `"adjust_config"`: transition to `BLOCKED_CONFIG` with `next_action = <config_guidance>`
    - `"abandon"`: transition to `FAILED` with the diagnosis `root_cause` as the terminal error
  - In all cases, append remote failure learnings to `state.key_learnings` before transitioning
  - Remote retries are the backend's responsibility via `remote_queue.retry_policy`; the orchestrator never re-enqueues a failed batch

### `ANALYZE_RESULTS`

- Call `remote_queue.results_command`
- Dispatch `metaopt-results-analysis` as one `strong_reasoner` subagent to compare the result against the aggregate baseline and extract learnings
- If the aggregate result clears `objective.improvement_threshold` in the configured direction, update the baseline and reset `no_improve_iterations` to `0`
- Otherwise leave the baseline unchanged and increment `no_improve_iterations`
- Update completed experiments and learnings in both cases

### `ROLL_ITERATION`

- Dispatch `metaopt-proposal-rollover` as one `strong_reasoner` subagent (inline dispatch — no slot consumed)
- Input: `next_proposals`, fresh `key_learnings`, completed experiment results, and updated baseline
- Output: filtered carry-over proposals with duplicates, invalidated ideas, and overlaps removed plus short rationale for each removal
- Move the filtered survivors into `current_proposals`
- Clear `next_proposals`
- Increment iteration counters
- Clear `selected_experiment` (set to `null`) after persisting the completed experiment record to `completed_experiments`
- Check stop conditions using the aggregate metric
- Emit the iteration report using the contract in `references/contracts.md`
- Transition to `QUIESCE_SLOTS` regardless of whether the campaign continues or stops

### `QUIESCE_SLOTS`

- Stop launching new work
- Persist any finished slot output before changing slot ownership
- Wait up to a 60-second drain window for in-flight slots to complete
- cancel leftovers after the 60-second drain window
- record cancellation reasons in state and append any mechanical patch-application outcome to `apply_results` in `local_changeset`
- If the campaign continues, set `machine_state = MAINTAIN_BACKGROUND_POOL`, keep `status = RUNNING`, and re-invoke `ml-metaoptimization`
- If the campaign stops, transition to `COMPLETE`

### Terminal States

- `COMPLETE`: emit the final report using the contract in `references/contracts.md` after all slots have already been drained or canceled, remove the `AGENTS.md` hook, delete `.ml-metaopt/state.json`, and stop
- `BLOCKED_CONFIG`: remove the `AGENTS.md` hook, leave state and artifacts intact so the campaign can resume after config repair, and stop
- `FAILED`: remove the `AGENTS.md` hook, write the terminal error, preserve state, and stop

All three terminal states remove the `AGENTS.md` hook using the same operation as the identity-drift path in `HYDRATE_STATE`: strip only the `<!-- ml-metaoptimization:begin -->...<!-- ml-metaoptimization:end -->` block. The difference is that terminal-state cleanup transitions the machine to a final state (`COMPLETE`, `BLOCKED_CONFIG`, or `FAILED`), whereas the identity-drift path is a hard stop that does not advance the state machine.

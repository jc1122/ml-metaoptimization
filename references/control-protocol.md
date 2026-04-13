# Control Protocol

## Core Rule

The orchestrator invokes the governing control agent for the current machine_state, reads the handoff JSON it writes to .ml-metaopt/handoffs/, and executes exactly one directive mechanically. The orchestrator never performs semantic decisions -- it only validates and executes.

## Handoff File Naming

Handoff files are written to:

    .ml-metaopt/handoffs/<agent-name>-<machine_state>.json

Examples:
- .ml-metaopt/handoffs/metaopt-load-campaign-LOAD_CAMPAIGN.json
- .ml-metaopt/handoffs/metaopt-remote-execution-control-WAIT_FOR_SWEEP.json

## Handoff Envelope Schema

Every control-agent handoff must conform to this envelope:

    {
      "recommended_next_machine_state": "LAUNCH_SWEEP or null",
      "state_patch": { "<field>": "<value>" },
      "directive": {
        "type": "launch_sweep",
        "payload": { }
      }
    }

| Field | Type | Constraint |
|-------|------|------------|
| recommended_next_machine_state | string or null | Valid machine state or null |
| state_patch | object | Only keys from STATE_PATCH_OWNERSHIP for this agent |
| directive.type | string | One of: launch_sweep, poll_sweep, run_smoke_test, none |
| directive.payload | object | Action-specific fields |

## Orchestrator Execution Sequence

On each reinvocation, the orchestrator executes these 8 steps in order:

### Step 1 -- Read state

Read .ml-metaopt/state.json to determine machine_state.

### Step 2 -- Determine phase

- If the latest handoff for the current state has recommended_next_machine_state = null: invoke the gate phase (the agent is polling or waiting)
- If no handoff exists for the current state: invoke the plan phase (first entry into this state)
- For single-phase states: invoke the single phase directly

### Step 3 -- Invoke governing control agent

Dispatch the governing control agent as a subagent. Wait for it to write its handoff file to .ml-metaopt/handoffs/.

### Step 4 -- Validate handoff

- Verify state_patch keys are in STATE_PATCH_OWNERSHIP for the invoking agent
- Verify directive.type is in the allowed directive actions list
- On validation failure: transition to BLOCKED_PROTOCOL

### Step 5 -- Execute directive

Execute the single directive mechanically:
- launch_sweep: dispatch skypilot-wandb-worker, write result to .ml-metaopt/worker-results/launch-sweep.json
- poll_sweep: dispatch skypilot-wandb-worker, write result to .ml-metaopt/worker-results/poll-sweep.json
- run_smoke_test: dispatch skypilot-wandb-worker, write result to .ml-metaopt/worker-results/smoke-test.json
- none: no operation

Terminal cleanup (remove_agents_hook, delete_state_file, emit_final_report, emit_iteration_report) is orchestrator-internal bookkeeping triggered by transitioning to a terminal state (COMPLETE, BLOCKED_CONFIG, BLOCKED_PROTOCOL, FAILED). The orchestrator performs these steps directly without dispatching an agent or requiring a directive. Specifically:
- On any terminal state: remove_agents_hook — remove the ml-metaoptimization marked block from AGENTS.md
- On COMPLETE only: emit_final_report — write .ml-metaopt/final_report.md, then delete_state_file — delete .ml-metaopt/state.json

### Step 6 -- Apply state patch

Apply state_patch fields from the handoff to .ml-metaopt/state.json. Each key overwrites the corresponding top-level state field.

### Step 7 -- Set machine state

If recommended_next_machine_state is non-null, set machine_state to that value. Derive status from the new machine_state:
- Terminal states map 1:1 (COMPLETE, BLOCKED_CONFIG, BLOCKED_PROTOCOL, FAILED)
- All non-terminal states set status = RUNNING

If recommended_next_machine_state is null, machine_state stays unchanged.

### Step 8 -- Persist state

Write the updated state to .ml-metaopt/state.json. State must be persisted after every transition.

## Phase Conventions

| State(s) | Phase(s) | Description |
|----------|----------|-------------|
| LOAD_CAMPAIGN | single (validate) | Validate YAML, check preflight, compute identity hash |
| HYDRATE_STATE | single (hydrate) | Initialize or resume state, verify worker availability |
| IDEATE | plan_ideation | Dispatch ideation workers, manage proposal pool |
| WAIT_FOR_PROPOSALS | gate_ideation | Check proposal count against threshold |
| SELECT_AND_DESIGN_SWEEP | plan_select_design then finalize_select_design | Pick best proposal, finalize sweep config |
| LOCAL_SANITY | single (gate_local_sanity) | Emit run_smoke_test, read result, advance or fail |
| LAUNCH_SWEEP | single (plan_launch) | Emit launch_sweep, persist sweep state |
| WAIT_FOR_SWEEP | poll (loops) | Emit poll_sweep, check completion/failure/budget |
| ANALYZE | single (analyze) | Dispatch analysis worker, update baseline |
| ROLL_ITERATION | single (roll) | Filter proposals, check stops, emit report |

## Null recommended_next_machine_state

When a control agent sets recommended_next_machine_state to null:
- The orchestrator stays in the current machine_state
- On the next session/reinvocation, the orchestrator re-invokes the same control agent
- Primary use case: WAIT_FOR_SWEEP polling -- each poll returns null until the sweep completes, at which point it returns ANALYZE

## State-Patch Validation Rules

The orchestrator maintains a STATE_PATCH_OWNERSHIP map that declares which state fields each control agent may write. On every handoff:

1. Extract all keys from state_patch
2. Verify each key is in the allowed set for the invoking agent
3. If any key is unauthorized: reject the entire handoff and transition to BLOCKED_PROTOCOL

Control agents must never write status in state_patch -- status is derived by the orchestrator from machine_state during Step 7.

## AGENTS.md Hook

metaopt-hydrate-state manages the AGENTS.md resume hook. On initialization it creates AGENTS.md if absent and appends the marked block:

    ## Active task
    .ml-metaopt/state.json exists. Inspect its status.
    If status is RUNNING, invoke the ml-metaoptimization skill immediately,
    resume from machine_state, and do not ask the user for campaign inputs.
    If status is terminal, invoke the ml-metaoptimization skill once so terminal
    cleanup can run through control-agent directives; do not execute next_action.

On terminal states, the governing control agent emits a remove_agents_hook directive and the orchestrator executes it. The orchestrator never appends or removes this block autonomously.

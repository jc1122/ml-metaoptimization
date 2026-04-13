# Dispatch Guide

This document specifies the per-state dispatch rules. It is the authoritative reference for which workers are dispatched, how they are dispatched, and what the orchestrator does with the results.

For worker lane contracts (inputs, outputs, drift rules), see references/worker-lanes.md. For the control-agent handoff protocol, see references/control-protocol.md.

## LOAD_CAMPAIGN

**Governing agent:** metaopt-load-campaign

**Dispatch:** Inline subagent. No worker slot.

**Orchestrator action:**
1. Invoke metaopt-load-campaign as subagent
2. Read handoff from .ml-metaopt/handoffs/metaopt-load-campaign-LOAD_CAMPAIGN.json
3. Execute directive (typically none)
4. Apply state_patch and transition

## HYDRATE_STATE

**Governing agent:** metaopt-hydrate-state

**Dispatch:** Inline subagent. No worker slot.

**Orchestrator action:**
1. Invoke metaopt-hydrate-state as subagent
2. Read handoff from .ml-metaopt/handoffs/metaopt-hydrate-state-HYDRATE_STATE.json
3. Execute directive (typically none; may create AGENTS.md hook)
4. Apply state_patch and transition

## IDEATE

**Governing agent:** metaopt-background-control (plan_background_work phase)

**Dispatch:** Background workers via launch_requests from the handoff.

**Worker:** metaopt-ideation-worker
**Slot class:** background
**Model class:** general_worker

**Orchestrator action:**
1. Invoke metaopt-background-control as subagent
2. Read handoff -- it contains launch_requests for metaopt-ideation-worker agents
3. Dispatch the requested number of ideation workers (up to proposal_policy.current_target)
4. Each worker writes its result to .ml-metaopt/worker-results/
5. Apply state_patch and transition to WAIT_FOR_PROPOSALS

**Constraint:** Number of concurrent ideation workers = proposal_policy.current_target minus existing proposals. Do not launch more once the pool is full.

## WAIT_FOR_PROPOSALS

**Governing agent:** metaopt-background-control (gate_background_work phase)

**Dispatch:** No new dispatch. Gate check only.

**Orchestrator action:**
1. Invoke metaopt-background-control as subagent
2. Read handoff -- gate checks whether current_proposals has reached threshold
3. If threshold not met: recommended_next_machine_state = IDEATE (loop back for more workers)
4. If threshold met: recommended_next_machine_state = SELECT_AND_DESIGN_SWEEP
5. Apply state_patch and transition

## SELECT_AND_DESIGN_SWEEP

**Governing agent:** metaopt-select-design

**Dispatch:** Two-phase workflow with an intermediate worker dispatch.

**Worker:** `metaopt-selection-worker`
**Slot class:** `selection`
**Model class:** `strong_reasoner`

### Phase 1 — Plan (`plan_select_design`)

**Orchestrator action:**
1. Invoke metaopt-select-design as subagent (plan phase)
2. Agent validates preconditions and freezes the proposal pool
3. Agent writes task file to `.ml-metaopt/tasks/select-design-iter-<N>.md` for `metaopt-selection-worker`
4. Read handoff -- `recommended_next_machine_state: SELECT_AND_DESIGN_SWEEP` (stays in same state)
5. Dispatch `metaopt-selection-worker` with the task file path
6. Worker reads frozen proposals, key learnings, and objective context from the task file
7. Worker writes result to `.ml-metaopt/worker-results/select-design-iter-<N>.json`

**Worker result fields:**
- `winning_proposal` — dict including `proposal_id` matching an entry in the frozen proposal pool
- `sweep_config` — valid WandB sweep config dict (method, metric, parameters)
- `ranking_rationale` — string explaining selection reasoning

### Phase 2 — Finalize (`finalize_select_design`)

**Orchestrator action:**
1. Re-invoke metaopt-select-design as subagent (finalize phase) with worker result available
2. Agent validates that `winning_proposal.proposal_id` matches frozen `current_proposals`
3. Agent validates `sweep_config` is a non-empty dict
4. Read handoff -- `recommended_next_machine_state: LOCAL_SANITY`
5. Apply state_patch (sets `selected_sweep` with `proposal_id` and `sweep_config`, freezes `proposal_cycle.current_pool_frozen = true`)
6. Transition to LOCAL_SANITY

## LOCAL_SANITY

**Governing agent:** metaopt-remote-execution-control (gate_local_sanity phase)

**Dispatch:** The orchestrator executes the run_smoke_test directive directly by dispatching skypilot-wandb-worker. No subagent slot -- this is a directive execution.

**Orchestrator action:**
1. Invoke metaopt-remote-execution-control as subagent
2. Read handoff -- it emits a run_smoke_test directive
3. Execute the directive: dispatch skypilot-wandb-worker with the smoke test command
4. Write result to .ml-metaopt/worker-results/smoke-test.json
5. Re-invoke the control agent with the result
6. If exit_code = 0 and timed_out = false: transition to LAUNCH_SWEEP
7. If exit_code != 0 or timed_out = true: transition to FAILED

**Hard constraint:** 60-second timeout, not configurable. No remediation loop.

## LAUNCH_SWEEP

**Governing agent:** metaopt-remote-execution-control (plan_launch phase)

**Dispatch:** The orchestrator executes the launch_sweep directive by dispatching skypilot-wandb-worker.

**Orchestrator action:**
1. Invoke metaopt-remote-execution-control as subagent
2. Read handoff -- it emits a launch_sweep directive with sweep_config, wandb credentials, compute params
3. Dispatch skypilot-wandb-worker with the launch_sweep payload
4. Write result to .ml-metaopt/worker-results/launch-sweep.json
5. Apply state_patch (sets current_sweep with sweep_id, sweep_url, sky_job_ids, launched_at)
6. Transition to WAIT_FOR_SWEEP

## WAIT_FOR_SWEEP

**Governing agent:** metaopt-remote-execution-control (poll phase)

**Dispatch:** The orchestrator executes the poll_sweep directive by dispatching skypilot-wandb-worker on each session.

**Orchestrator action:**
1. Invoke metaopt-remote-execution-control as subagent
2. Read handoff -- it emits a poll_sweep directive
3. Dispatch skypilot-wandb-worker with sweep_id, sky_job_ids, budget params
4. Write result to .ml-metaopt/worker-results/poll-sweep.json
5. Re-invoke the control agent with the poll result
6. Control agent evaluates:
   - sweep_status = running: recommended_next_machine_state = null (stay, poll again next session)
   - sweep_status = completed: recommended_next_machine_state = ANALYZE
   - sweep_status = failed: recommended_next_machine_state = FAILED
   - sweep_status = budget_exceeded: recommended_next_machine_state = BLOCKED_CONFIG
7. Apply state_patch (updates current_sweep.cumulative_spend_usd) and transition accordingly

## ANALYZE

**Governing agent:** metaopt-remote-execution-control (analyze phase)

**Dispatch:** Auxiliary worker via launch_requests from the handoff.

**Worker:** metaopt-analysis-worker
**Slot class:** auxiliary
**Model class:** strong_reasoner

**Orchestrator action:**
1. Invoke metaopt-remote-execution-control as subagent
2. Read handoff -- it contains launch_requests for metaopt-analysis-worker
3. Dispatch metaopt-analysis-worker with best run data, baseline, learnings
4. Worker writes result to .ml-metaopt/worker-results/sweep-analysis-iter-<N>.json
5. Re-invoke the control agent with the analysis result
6. Apply state_patch (updates baseline if improved, appends key_learnings)
7. Transition to ROLL_ITERATION

## ROLL_ITERATION

**Governing agent:** metaopt-iteration-close-control (roll phase)

**Dispatch:** Inline subagent. No worker slot.

**Orchestrator action:**
1. Invoke metaopt-iteration-close-control as subagent
2. Agent filters next_proposals, increments current_iteration, checks stop conditions
3. Read handoff -- contains state_patch and directive (emit_iteration_report or emit_final_report)
4. Execute directive
5. Apply state_patch (resets current_sweep = null, selected_sweep = null, moves proposals)
6. Transition:
   - Stop condition met: COMPLETE (with remove_agents_hook, delete_state_file, emit_final_report directives)
   - Budget exhausted: BLOCKED_CONFIG (with remove_agents_hook directive)
   - Continue: IDEATE (next iteration)

## Terminal States

When entering any terminal state, the control agent emits cleanup directives. The orchestrator executes them in the order listed:

**COMPLETE:**
1. emit_final_report -- write .ml-metaopt/final_report.md
2. remove_agents_hook -- remove ml-metaoptimization block from AGENTS.md
3. delete_state_file -- delete .ml-metaopt/state.json

**BLOCKED_CONFIG, BLOCKED_PROTOCOL, FAILED:**
1. remove_agents_hook -- remove ml-metaoptimization block from AGENTS.md
2. State is preserved for debugging

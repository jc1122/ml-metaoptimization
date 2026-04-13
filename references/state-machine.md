# State Machine

## State List

| State | Type | Description |
|-------|------|-------------|
| LOAD_CAMPAIGN | init | Validate campaign YAML, check preflight readiness, compute identity hash |
| HYDRATE_STATE | init | Initialize or resume state, verify worker availability, crash recovery |
| IDEATE | running | Dispatch ideation workers to generate sweep search space proposals |
| WAIT_FOR_PROPOSALS | running | Gate: require proposal_policy.current_target proposals before advancing |
| SELECT_AND_DESIGN_SWEEP | running | Pick best proposal, refine and freeze sweep config for launch |
| LOCAL_SANITY | running | Run smoke test with 60-second hard timeout |
| LAUNCH_SWEEP | running | Create WandB sweep, launch SkyPilot agents on Vast.ai |
| WAIT_FOR_SWEEP | running | Poll sweep status, watchdog for hung agents, budget enforcement |
| ANALYZE | running | Read best WandB run, compare against baseline, extract learnings |
| ROLL_ITERATION | running | Filter proposals, increment iteration, check stop conditions |
| COMPLETE | terminal | Stop condition met; emit final report, cleanup |
| BLOCKED_CONFIG | terminal | User-actionable config issue (budget cap, bad YAML, missing preflight) |
| BLOCKED_PROTOCOL | terminal | Protocol-level violation the skill cannot recover from |
| FAILED | terminal | Unrecoverable error (smoke test failed, all sweep agents crashed) |

## Control Agent Dispatch Table

| State(s) | Governing Agent | Phase(s) |
|----------|----------------|----------|
| LOAD_CAMPAIGN | metaopt-load-campaign | single (validate) |
| HYDRATE_STATE | metaopt-hydrate-state | single (hydrate) |
| IDEATE, WAIT_FOR_PROPOSALS | metaopt-background-control | plan_background_work, gate_background_work |
| SELECT_AND_DESIGN_SWEEP | metaopt-select-design | plan_select_design, finalize_select_design |
| LOCAL_SANITY | metaopt-remote-execution-control | single (gate_local_sanity) |
| LAUNCH_SWEEP | metaopt-remote-execution-control | single (plan_launch) |
| WAIT_FOR_SWEEP | metaopt-remote-execution-control | poll_sweep (loops until non-null transition) |
| ANALYZE | metaopt-remote-execution-control | single (analyze) |
| ROLL_ITERATION | metaopt-iteration-close-control | plan_roll_iteration, gate_roll_iteration |

## State Semantics

### LOAD_CAMPAIGN

Governed by metaopt-load-campaign. Validates campaign YAML against the schema in references/dependencies.md, checks the preflight readiness artifact (.ml-metaopt/preflight-readiness.json), and computes the campaign_identity_hash. Transitions to BLOCKED_CONFIG on invalid config, missing preflight, or identity hash mismatch on resume.

### HYDRATE_STATE

Governed by metaopt-hydrate-state. Initializes a fresh state file or resumes from an existing one. If state.current_sweep.sweep_id exists (crash recovery), sets recommended_next_machine_state to WAIT_FOR_SWEEP to reconnect to that WandB sweep rather than launching a new one. Verifies worker availability via the skills manifest. Creates the AGENTS.md resume hook if absent.

### IDEATE

Governed by metaopt-background-control (plan_background_work phase). Dispatches metaopt-ideation-worker background agents that produce WandB sweep search space proposals. Each proposal includes a WandB-formatted sweep config with parameter distributions and search method. Agents run until the proposal pool reaches proposal_policy.current_target.

### WAIT_FOR_PROPOSALS

Governed by metaopt-background-control (gate_background_work phase). Gate: checks whether current_proposals has reached proposal_policy.current_target. If not, stays in this state (returns to IDEATE on next reinvocation for more workers). If threshold met, advances to SELECT_AND_DESIGN_SWEEP.

### SELECT_AND_DESIGN_SWEEP

Governed by metaopt-select-design. A single inline agent picks the best proposal from the pool given prior learnings and baseline context, then refines it into a final WandB sweep config ready for launch. Freezes proposal_cycle.current_pool_frozen = true. Select and design are one combined step.

### LOCAL_SANITY

Governed by metaopt-remote-execution-control (gate_local_sanity phase). Emits a run_smoke_test directive with a 60-second hard time limit (not configurable). Goal: confirm the training script starts, loads data, and executes a forward+backward pass without crashing. If exit_code != 0 or timed_out = true, transition to FAILED. No remediation loop -- fail fast.

### LAUNCH_SWEEP

Governed by metaopt-remote-execution-control (plan_launch phase). Emits a launch_sweep directive. The orchestrator dispatches skypilot-wandb-worker, which creates the WandB sweep and launches SkyPilot agents on Vast.ai. Persists sweep_id, sweep_url, and sky_job_ids to state via state_patch.

### WAIT_FOR_SWEEP

Governed by metaopt-remote-execution-control (poll phase). Emits a poll_sweep directive on each session. Each poll also acts as a watchdog: detects hung agents (no WandB logs for idle_timeout_minutes), kills their SkyPilot jobs, and enforces the budget cap. Returns recommended_next_machine_state = null while sweep is running. Transitions to ANALYZE when sweep completes, to FAILED if all agents crash, to BLOCKED_CONFIG if budget is exceeded.

### ANALYZE

Governed by metaopt-remote-execution-control (analyze phase). Dispatches metaopt-analysis-worker to read the best WandB run from the sweep, compare against baseline using direction-aware comparison (see references/contracts.md Section 5), update baseline if improved, and extract learnings.

### ROLL_ITERATION

Governed by metaopt-iteration-close-control (plan_roll_iteration, gate_roll_iteration phases). Computes proposal rollover inline during plan, then gates the result: filters next_proposals by proposal_id against completed_iterations, enriches merged proposals, increments current_iteration, checks all stop conditions, resets current_sweep and selected_sweep to null, and emits an iteration report. Transitions to COMPLETE if a stop condition is met, to BLOCKED_CONFIG if budget is exhausted, or back to IDEATE for the next iteration.

## Stop Conditions

Checked by metaopt-iteration-close-control during ROLL_ITERATION:

| Condition | Check | Transition |
|-----------|-------|------------|
| Target metric reached | Direction-aware: >= target_metric (maximize) or <= target_metric (minimize) | COMPLETE |
| Max iterations reached | current_iteration >= stop_conditions.max_iterations | COMPLETE |
| No improvement plateau | no_improve_iterations >= stop_conditions.max_no_improve_iterations | COMPLETE |
| Budget exhausted | sum(completed_iterations[].spend_usd) >= compute.max_budget_usd | BLOCKED_CONFIG |

## Terminal State Cleanup

When transitioning to a terminal state, the governing control agent emits cleanup directives:

| Terminal State | Required Directives |
|---------------|-------------------|
| COMPLETE | remove_agents_hook, emit_final_report |
| BLOCKED_CONFIG | remove_agents_hook, emit_final_report |
| BLOCKED_PROTOCOL | remove_agents_hook |
| FAILED | remove_agents_hook |

All terminal states remove the AGENTS.md hook to prevent infinite re-invocation loops. COMPLETE and BLOCKED_CONFIG also emit a final report.

## State Transition Diagram

    LOAD_CAMPAIGN
      -> BLOCKED_CONFIG (invalid config or missing preflight)
      -> HYDRATE_STATE (campaign valid)
    HYDRATE_STATE
      -> IDEATE (fresh init)
      -> WAIT_FOR_SWEEP (crash recovery with active sweep)
      -> BLOCKED_CONFIG (identity mismatch or missing required skill)
      -> <resumed machine_state> (resume from prior state)
    IDEATE
      -> WAIT_FOR_PROPOSALS
    WAIT_FOR_PROPOSALS
      -> IDEATE (not enough proposals, need more workers)
      -> SELECT_AND_DESIGN_SWEEP (threshold met)
    SELECT_AND_DESIGN_SWEEP
      -> LOCAL_SANITY
    LOCAL_SANITY
      -> LAUNCH_SWEEP (smoke test passed)
      -> FAILED (exit_code != 0 or timed_out)
    LAUNCH_SWEEP
      -> WAIT_FOR_SWEEP
    WAIT_FOR_SWEEP
      -> WAIT_FOR_SWEEP (sweep still running, poll again)
      -> ANALYZE (sweep completed)
      -> FAILED (all agents crashed)
      -> BLOCKED_CONFIG (budget exceeded)
    ANALYZE
      -> ROLL_ITERATION
    ROLL_ITERATION
      -> COMPLETE (stop condition met)
      -> BLOCKED_CONFIG (budget exhausted)
      -> IDEATE (next iteration)

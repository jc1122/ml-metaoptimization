---
name: metaopt-iteration-close-control
description: Roll the iteration — filter/carry proposals, check stop conditions (direction-aware), emit iteration report, advance to IDEATE or COMPLETE.
model: claude-sonnet-4
tools:
  - read
  - search
  - execute
user-invocable: false
---

# metaopt-iteration-close-control

## Purpose

You are the ROLL_ITERATION agent for the `ml-metaoptimization` v4 orchestrator. You perform all iteration-close logic in a single phase: filter and carry forward proposals, increment the iteration counter, reset sweep state, check all stop conditions, emit an iteration report, and decide whether to continue (→ IDEATE) or stop (→ COMPLETE/BLOCKED_CONFIG).

In v4, this agent absorbs the work previously done by a separate `metaopt-rollover-worker`. There is no separate dispatch — you do the filtering inline.

## Inputs

1. **State**: `.ml-metaopt/state.json` — read all fields, especially: `current_iteration`, `current_proposals`, `next_proposals`, `key_learnings`, `completed_iterations`, `baseline`, `no_improve_iterations`, `current_sweep`, `selected_sweep`, `objective_snapshot`
2. **Campaign**: `ml_metaopt_campaign.yaml` — read `stop_conditions`, `compute.max_budget_usd`, `objective`

## Steps

### Step 1: Filter next_proposals

Read `state.next_proposals`. For each proposal, check:

1. **Duplicate of completed iteration**: compare the proposal's `sweep_config.parameters` against `completed_iterations[].sweep_config.parameters`. If the parameter names and distributions are substantially similar (same parameters, overlapping ranges covering > 80% of the same space), discard it with reason "duplicate of iteration <N>".

2. **Contradicts key_learnings**: check if the proposal's search space contradicts any learning. For example, if a learning says "lr > 0.01 causes divergence" but the proposal has `lr.max = 0.1`, discard it with reason "contradicts learning: <learning>".

Keep proposals that pass both checks.

### Step 2: Carry proposals forward

Build the new `current_proposals` list:
- Start with the filtered `next_proposals` from Step 1.
- Clear `next_proposals` to `[]`.

### Step 3: Check stop conditions

Evaluate ALL stop conditions. The FIRST matching condition determines the outcome:

1. **Target metric reached** (direction-aware):
   - If `objective.direction == "maximize"` AND `baseline.value >= stop_conditions.target_metric` → COMPLETE
   - If `objective.direction == "minimize"` AND `baseline.value <= stop_conditions.target_metric` → COMPLETE
   - If `stop_conditions.target_metric` is not set, skip this check.

2. **Max iterations reached**:
   - If `current_iteration >= stop_conditions.max_iterations` → COMPLETE

3. **No improvement plateau**:
   - If `no_improve_iterations >= stop_conditions.max_no_improve_iterations` → COMPLETE

4. **Budget exhausted**:
   - Sum `cumulative_spend_usd` across all `completed_iterations`. If total `>= compute.max_budget_usd` → BLOCKED_CONFIG: `"Total spend $<N> reached budget cap of $<max>"`

If NO stop condition is met → continue to next iteration.

### Step 4: Build state_patch

**If continuing (no stop condition met):**

```json
{
  "current_iteration": "<current_iteration + 1>",
  "current_sweep": null,
  "selected_sweep": null,
  "current_proposals": "<filtered next_proposals from Step 2>",
  "next_proposals": [],
  "proposal_cycle": {
    "cycle_id": "iter-<current_iteration + 1>-cycle-1",
    "current_pool_frozen": false
  }
}
```

**If stopping (COMPLETE):**

```json
{
  "current_sweep": null,
  "selected_sweep": null,
  "next_action": "Campaign complete. <stop reason>. Best metric: <baseline.value>. See .ml-metaopt/final_report.md"
}
```

**If stopping (BLOCKED_CONFIG):**

```json
{
  "next_action": "<blocking reason>"
}
```

### Step 5: Write iteration report (in-process)

Always write an iteration report as an in-process action using the `execute` tool (even when stopping). This is NOT a directive — the agent writes it directly:

Write to `.ml-metaopt/iteration-report-<current_iteration>.md` with content summarizing:
- Iteration number
- Best metric value (`baseline.value`)
- Spend this iteration (`current_sweep.cumulative_spend_usd`)
- Sweep URL (`current_sweep.sweep_url`)
- Selected proposal rationale

### Step 6: Cleanup on stop (orchestrator-internal)

If the campaign is stopping (COMPLETE or BLOCKED_CONFIG), terminal cleanup (removing the AGENTS.md hook, deleting the state file, writing the final report) is orchestrator-internal bookkeeping triggered by transitioning to COMPLETE. The agent does NOT emit directives for these — the orchestrator performs them directly after seeing the terminal `recommended_next_machine_state`.

## Output

Write handoff to: `.ml-metaopt/handoffs/metaopt-iteration-close-control-ROLL_ITERATION.json`

**Continuing:**
```json
{
  "recommended_next_machine_state": "IDEATE",
  "state_patch": { "...from Step 4..." },
  "directive": { "type": "none" },
  "summary": "Iteration <N> complete. Improved: <yes/no>. Continuing to iteration <N+1>.",
  "filtered_proposals": { "kept": "<count>", "discarded": "<count>", "discard_reasons": ["..."] }
}
```

**Stopping (COMPLETE — target met, max iterations, or no-improvement plateau):**

```json
{
  "recommended_next_machine_state": "COMPLETE",
  "state_patch": {
    "current_sweep": null,
    "selected_sweep": null,
    "next_action": "Campaign complete. <stop reason>. Best metric: <baseline.value>. See .ml-metaopt/final_report.md"
  },
  "directive": { "type": "none" },
  "stop_reason": "<which condition triggered>",
  "summary": "Campaign complete after <N> iterations. Stop reason: <reason>. Best: <metric>=<value>"
}
```

**Stopping (BLOCKED_CONFIG — budget exhausted):**

```json
{
  "recommended_next_machine_state": "BLOCKED_CONFIG",
  "state_patch": {
    "next_action": "Budget cap exceeded: <amount> USD spent of <max> USD limit. Increase compute.max_budget_usd or reduce num_sweep_agents."
  },
  "directive": { "type": "none" },
  "stop_reason": "budget_exhausted",
  "summary": "Budget exhausted after <N> iterations. Total spend: $<amount> of $<max> cap."
}
```

## Rules

- Do NOT write to `.ml-metaopt/state.json` directly. All changes via `state_patch`.
- Do NOT dispatch workers. Rollover filtering is done inline by this agent.
- Do NOT modify `key_learnings` or `completed_iterations` — those were set by the ANALYZE phase.
- Increment `current_iteration` ONLY when the campaign will continue. Do not increment on stop.
- Stop condition checks are direction-aware: `>=` for maximize, `<=` for minimize.
- When discarding proposals, log the reason for each discard in the handoff for auditability.
- The iteration report is ALWAYS written (in-process via `execute` tool), whether continuing or stopping.

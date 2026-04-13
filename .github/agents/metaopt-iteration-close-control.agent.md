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

You are the ROLL_ITERATION agent for the `ml-metaoptimization` v4 orchestrator. You perform iteration-close logic in two phases (`plan_roll_iteration` then `gate_roll_iteration`): compute proposal rollover inline, filter and carry forward proposals, increment the iteration counter, reset sweep state, check all stop conditions, emit an iteration report, and decide whether to continue (→ IDEATE) or stop (→ COMPLETE/BLOCKED_CONFIG).

In v4, this agent absorbs the work previously done by a separate `metaopt-rollover-worker`. There is no separate dispatch — rollover filtering is computed inline during `plan_roll_iteration`, then gated in `gate_roll_iteration`.

## Inputs

1. **State**: `.ml-metaopt/state.json` — read all fields, especially: `current_iteration`, `current_proposals`, `next_proposals`, `key_learnings`, `completed_iterations`, `baseline`, `no_improve_iterations`, `current_sweep`, `selected_sweep`, `objective_snapshot`
2. **Load handoff**: `.ml-metaopt/handoffs/metaopt-load-campaign-LOAD_CAMPAIGN.json` — read `stop_conditions`, `compute.max_budget_usd`

## Steps

### Step 1: Compute rollover inline (`plan_roll_iteration`)

Read `state.next_proposals`. Apply **mechanical filtering**: drop any proposal whose `proposal_id` already appears in `completed_iterations[].proposal_id`. All remaining proposals are carried forward.

This produces a rollover result file (`rollover-iter-<N>.json`) containing:
- `filtered_proposals` — list of proposal IDs that were dropped (already executed)
- `merged_proposals` — list of proposal dicts that passed filtering
- `needs_fresh_ideation` — `true` if `len(merged) < proposal_policy.current_target`
- `summary` — human-readable description

> **Note:** Semantic filtering (e.g. contradiction against `key_learnings` or parameter-space similarity) is not performed by the script. If needed, the agent layer may add semantic checks before gating.

### Step 2: Gate rollover and carry proposals forward (`gate_roll_iteration`)

Read the rollover result file. Build the new `current_proposals` list:
- Start with `filtered_proposals` from the rollover result.
- Enrich each `merged_proposals` entry with a new `proposal_id` (sequenced from the campaign), `source_slot_id: "rollover"`, `creation_iteration`, and `created_at` timestamp, then append to `current_proposals`.
- Clear `next_proposals` to `[]`.

### Step 3: Check stop conditions

Evaluate ALL stop conditions. The FIRST matching condition determines the outcome:

1. **Target metric reached** (direction-aware):
   - If `baseline` is null, skip this check (no metric to compare).
   - If `stop_conditions.target_metric` is not set, skip this check.
   - If `objective_snapshot.direction == "maximize"` AND `baseline.value >= stop_conditions.target_metric` → COMPLETE
   - If `objective_snapshot.direction == "minimize"` AND `baseline.value <= stop_conditions.target_metric` → COMPLETE

2. **Max iterations reached**:
   - If `current_iteration >= stop_conditions.max_iterations` → COMPLETE

3. **No improvement plateau**:
   - If `no_improve_iterations >= stop_conditions.max_no_improve_iterations` → COMPLETE

4. **Budget exhausted**:
   - Sum `spend_usd` across all `completed_iterations`. If total `>= compute.max_budget_usd` → BLOCKED_CONFIG: `"Total spend $<N> reached budget cap of $<max>"`

If NO stop condition is met → continue to next iteration.

### Step 4: Build state_patch

**If continuing (no stop condition met):**

```json
{
  "current_iteration": "<current_iteration + 1>",
  "current_sweep": null,
  "selected_sweep": null,
  "current_proposals": "<carried proposals from Step 2>",
  "next_proposals": [],
  "next_action": "maintain background pool",
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
  "next_action": "emit final report and remove orchestration hook"
}
```

**If stopping (BLOCKED_CONFIG):**

```json
{
  "next_action": "<blocking reason>"
}
```

### Step 5: Emit directives

Build the `directives` array (an ordered list of cleanup actions for the orchestrator to execute):

**Always emit:**
- `emit_iteration_report` — the orchestrator writes the iteration summary:
  ```json
  { "action": "emit_iteration_report", "report_type": "iteration", "iteration": "<current_iteration>" }
  ```

**If stopping (COMPLETE — target met, max iterations, or no-improvement plateau), also emit:**
- `remove_agents_hook` — remove the ml-metaoptimization block from `AGENTS.md`:
  ```json
  { "action": "remove_agents_hook", "agents_path": "AGENTS.md" }
  ```
- `emit_final_report` — write `.ml-metaopt/final_report.md`:
  ```json
  { "action": "emit_final_report", "report_type": "final" }
  ```

**If stopping (BLOCKED_CONFIG — budget exhausted), also emit:**
- `remove_agents_hook` (same as above)
- `emit_final_report` (same as above)

## Output

Write handoff to: `.ml-metaopt/handoffs/metaopt-iteration-close-control-ROLL_ITERATION.json`

**Continuing:**
```json
{
  "schema_version": 1,
  "continue_campaign": true,
  "recommended_next_machine_state": "IDEATE",
  "stop_reason": "",
  "state_patch": { "...from Step 4..." },
  "directives": [
    { "action": "emit_iteration_report", "report_type": "iteration", "iteration": "<N>" }
  ],
  "iteration_report": "=== Iteration <N> Report ===\n...",
  "warnings": [],
  "summary": "rollover semantics applied"
}
```

**Stopping (COMPLETE — target met, max iterations, or no-improvement plateau):**

```json
{
  "schema_version": 1,
  "continue_campaign": false,
  "recommended_next_machine_state": "COMPLETE",
  "stop_reason": "<which condition triggered>",
  "state_patch": {
    "current_sweep": null,
    "selected_sweep": null,
    "next_action": "emit final report and remove orchestration hook"
  },
  "directives": [
    { "action": "emit_iteration_report", "report_type": "iteration", "iteration": "<N>" },
    { "action": "remove_agents_hook", "agents_path": "AGENTS.md" },
    { "action": "emit_final_report", "report_type": "final" }
  ],
  "iteration_report": "=== Iteration <N> Report ===\n...",
  "warnings": [],
  "summary": "rollover semantics applied"
}
```

**Stopping (BLOCKED_CONFIG — budget exhausted):**

```json
{
  "schema_version": 1,
  "continue_campaign": false,
  "recommended_next_machine_state": "BLOCKED_CONFIG",
  "stop_reason": "budget_exhausted",
  "state_patch": {
    "next_action": "Budget cap exceeded: <amount> USD spent of <max> USD limit. Increase compute.max_budget_usd or reduce num_sweep_agents."
  },
  "directives": [
    { "action": "emit_iteration_report", "report_type": "iteration", "iteration": "<N>" },
    { "action": "remove_agents_hook", "agents_path": "AGENTS.md" },
    { "action": "emit_final_report", "report_type": "final" }
  ],
  "iteration_report": "=== Iteration <N> Report ===\n...",
  "warnings": [],
  "summary": "rollover semantics applied"
}
```

## Rules

- Do NOT write to `.ml-metaopt/state.json` directly. All changes via `state_patch`.
- Do NOT dispatch workers. Rollover filtering is done inline by this agent.
- Do NOT modify `key_learnings` or `completed_iterations` — those were set by the ANALYZE phase.
- Increment `current_iteration` ONLY when the campaign will continue. Do not increment on stop.
- Stop condition checks are direction-aware: `>=` for maximize, `<=` for minimize.
- When discarding proposals, log the reason for each discard in the handoff for auditability.
- The `emit_iteration_report` directive is ALWAYS emitted, whether continuing or stopping.

## Error Handling

### Malformed stop_conditions
If `stop_conditions` is missing from the campaign YAML, or required fields (`max_iterations`, `max_no_improve_iterations`) are absent or non-numeric, emit `BLOCKED_CONFIG` with `next_action: "stop_conditions in ml_metaopt_campaign.yaml is malformed — required fields: max_iterations (integer), max_no_improve_iterations (integer)"`. Do not attempt to evaluate stop conditions with missing data.

### Budget exceeded
If total `spend_usd` across `completed_iterations` meets or exceeds `compute.max_budget_usd`, emit `BLOCKED_CONFIG` (not `COMPLETE`) with the `remove_agents_hook` directive. The `next_action` must state the spend amount, the budget cap, and suggest increasing `compute.max_budget_usd`. This is a config issue, not a successful completion — the user must decide whether to increase the budget.

### Missing or null baseline during target_metric check
If `stop_conditions.target_metric` is set but `baseline` is `null` (no iteration has completed analysis yet), skip the target-metric check. Do not treat a null baseline as meeting or failing the target.

### No retry semantics
This agent runs inline once per iteration close. If it emits a terminal state (`COMPLETE` or `BLOCKED_CONFIG`), the orchestrator transitions immediately. There is no retry loop — a malformed stop_conditions error requires the user to fix the YAML and restart.

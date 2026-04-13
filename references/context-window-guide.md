# Context Window Guide for the Orchestrator

This document tells the orchestrator agent exactly which files to read, when,
and which files to skip entirely. It is derived from a full end-to-end simulation
of one complete iteration (LOAD_CAMPAIGN → IDEATE → SELECT → LOCAL_SANITY →
LAUNCH → WAIT → ANALYZE → ROLL_ITERATION → IDEATE/iter-2).

---

## TL;DR — read this, skip everything else

| Phase | Must read | Must NOT read |
|---|---|---|
| First turn ever | `SKILL.md`, `references/control-protocol.md`, `references/dispatch-guide.md`, `references/state-machine.md` | Everything else |
| Every subsequent turn | `state.json` (machine_state only), latest handoff JSON | Campaign YAML, all other reference docs, task files, result files |

---

## Phase 1 — Startup (read once, never again)

Read these **once** on your very first turn:

| File | Why |
|---|---|
| `SKILL.md` | Gives you the entry point, state machine overview, and tool list |
| `references/control-protocol.md` | Defines the handoff → script → state-patch loop you must execute every turn |
| `references/dispatch-guide.md` | Maps every machine state to the exact script, mode, and args to run |
| `references/state-machine.md` | Documents every legal state transition and stop condition |

These four files are all you need to understand the entire control flow.
Do **not** re-read them on subsequent turns — they do not change.

---

## Phase 2 — Every turn after startup

Your job is mechanical: read the current state, run one script, apply the patch.

### What to read

1. **`state.json`** — read only `.machine_state` (and `.next_action` if present).
   The full state is 1–2 KB and grows slowly, but you only need those two fields
   to decide which script to run. The script reads the full state internally.

2. **Latest handoff JSON** — the file your previous script wrote to
   `.ml-metaopt/handoffs/`. It is 330–1600 chars. Read the whole thing:
   it contains `recommended_next_machine_state`, `summary`, `state_patch`,
   `directives`, and `launch_requests` — everything you need.

That is all. Two reads per turn. Total context overhead: ~3 KB per turn.

### What NOT to read

| File / path | Reason to skip |
|---|---|
| `ml_metaopt_campaign.yaml` | Fully denormalized into the LOAD_CAMPAIGN handoff; never needed again |
| `references/contracts.md` | Internal schema used by Python scripts; you never validate JSON manually |
| `references/worker-lanes.md` | Worker-internal; describes what workers do, not what you do |
| `references/backend-contract.md` | Worker-internal; SkyPilot/WandB API shapes for the executor, not you |
| `.ml-metaopt/preflight-readiness.json` | Read by `load_campaign_handoff.py` internally; you never parse this artifact directly |
| `references/dependencies.md` | Used only during HYDRATE_STATE preflight; already done |
| `.ml-metaopt/tasks/*.md` | Worker task files — written by control scripts, consumed by workers, not you |
| `.ml-metaopt/worker-results/*.json` | Read by control scripts internally; you never parse these |
| `.ml-metaopt/executor-events/*.json` | Same — read by scripts, not by you |
| Handoffs older than the current turn | History is in `state.json`; old handoffs are stale |

---

## Handoff sizes measured in simulation

All handoffs are compact by design:

| State | Handoff size | Key output |
|---|---|---|
| LOAD_CAMPAIGN | 1551 chars | Campaign config denormalized; next → HYDRATE_STATE |
| HYDRATE_STATE | 1255 chars | State initialized; next → IDEATE |
| IDEATE | 948 chars | Background workers dispatched |
| WAIT_FOR_PROPOSALS | 1346 chars | Proposals collected; next → SELECT_AND_DESIGN_SWEEP |
| SELECT_AND_DESIGN_SWEEP (plan) | 508 chars | Pool frozen; next → SELECT_AND_DESIGN_SWEEP (finalize) |
| SELECT_AND_DESIGN_SWEEP (done) | 648 chars | Winner selected; next → LOCAL_SANITY |
| LOCAL_SANITY | 332 chars | Smoke test passed; next → LAUNCH_SWEEP |
| LAUNCH_SWEEP | 558 chars | Sweep live; next → WAIT_FOR_SWEEP |
| WAIT_FOR_SWEEP | 356 chars | Sweep done; next → ANALYZE |
| ANALYZE | 545 chars | Analysis dispatched then complete; next → ROLL_ITERATION |
| ROLL_ITERATION (gate) | 1308 chars | Rolled to next iteration; next → IDEATE |

`state.json` at end of iteration 1: **1585 chars** (grows by ~400–600 chars per iteration).

---

## Worker result status field contracts

These are gotchas discovered in simulation. The control scripts check exact field
names and values; if your simulated or real workers use different names, the gate
will stall.

| Result file | Required field | Required value |
|---|---|---|
| `worker-results/bg-{N}.json` (ideation) | `status` | `"completed"` |
| `executor-events/poll-sweep-iter-{N}.json` | `sweep_status` | `"completed"` or `"running"` or `"budget_exceeded"` |
| `worker-results/rollover-iter-{N}.json` | top-level keys | `filtered_proposals` (list), `merged_proposals` (list), `needs_fresh_ideation` (bool), `summary` (str) |
| `worker-results/select-design-iter-{N}.json` | top-level key | `winning_proposal` (object with `proposal_id`), `sweep_config`, `ranking_rationale` |
| `worker-results/sweep-analysis-iter-{N}.json` | flexible | any dict; stored verbatim in `completed_iterations` |
| `executor-events/smoke-test-iter-{N}.json` | `exit_code` | `0` for pass |
| `worker-results/launch-sweep-iter-{N}.json` | `sweep_id` | non-empty string to advance to WAIT_FOR_SWEEP |

---

## AGENTS.md hook — re-entry contract

After HYDRATE_STATE, the script writes `.ml-metaopt/AGENTS.md` (6 lines):

```
If state.json exists and machine_state != COMPLETE/BLOCKED_*/FAILED:
  Re-enter metaopt-hydrate-state skill to resume the campaign.
```

This is the only file the orchestrator reads on re-entry (besides `state.json`).
It is written once and never changes.

---

## Summary: orchestrator token budget per turn

| Read | Approx tokens |
|---|---|
| `state.json` (`.machine_state` field) | < 10 |
| Latest handoff JSON | 120–400 |
| **Total per turn** | **< 450** |

Startup reads (once): ~600 lines across 4 reference docs ≈ 2000 tokens.

The entire orchestrator context cost for a 5-iteration campaign is:
startup (~2000) + per-turn (~300 avg × ~25 turns) ≈ **~10 000 tokens total**,
compared to ~50 000+ if the orchestrator re-read reference docs every turn.

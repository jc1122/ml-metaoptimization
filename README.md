# ML Metaoptimization Skill Repo

This repository defines the `ml-metaoptimization` skill plus its supporting reference contracts and example campaign.
This repository is a contract-only scope for the `ml-metaoptimization` skill.
It pins the public docs, examples, and fixtures for the orchestration runtime.
It does not simulate a live Copilot host or remote queue backend.

## Validation

Install the validation dependency from the repo root:

```bash
python3 -m pip install --user -r requirements.txt
```

Run the validation suite from the repo root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

The tests pin the public contract for:
- the example campaign file
- backend stdout JSON payload shapes
- state-file fixtures and lifecycle pairing rules
- cross-document consistency between the skill and reference docs

## Ecosystem

This orchestrator delegates to the following worker targets:

- `metaopt-ideation-worker` — Step-3 ideation custom agent wrapper
- `metaopt-selection-worker` — Step-5 selection custom agent wrapper
- `metaopt-design-worker` — Step-6 design custom agent wrapper
- `metaopt-materialization-worker` — Step-7 materialization custom agent wrapper
- `metaopt-diagnosis-worker` — shared diagnosis custom agent wrapper
- `metaopt-analysis-worker` — remote-results analysis custom agent wrapper
- `metaopt-rollover-worker` — iteration rollover custom agent wrapper

Backend execution:
- [`hetzner-delegation`](https://github.com/jc1122/hetzner-delegation) — remote compute delegation skill
- [`ray-hetzner`](https://github.com/jc1122/ray-hetzner) — Ray cluster and queue runtime

Maintenance:
- [`repo-audit-refactor-optimize`](https://github.com/jc1122/repo-audit-refactor-optimize) — background maintenance lane

## Runtime Notes

`SKILL.md` describes the intended GitHub Copilot-style orchestration runtime.

`agents/openai.yaml` is separate catalog metadata for OpenAI/Codex-style runtimes. It exists so those runtimes can surface this skill in their own UI/catalog flows; it does not define Copilot dispatch behavior.

## Delegated Step 1

This repo now includes a dedicated Step-1 custom agent profile for Copilot runtimes at
`.github/agents/metaopt-load-campaign.agent.md`.

That agent wraps `scripts/load_campaign_handoff.py`, which:
- validates `ml_metaopt_campaign.yaml`
- rejects sentinel placeholders
- computes `campaign_identity_hash` and `runtime_config_hash`
- performs an advisory `.ml-metaopt/state.json` identity peek
- writes a non-authoritative handoff artifact to `.ml-metaopt/handoffs/load_campaign.latest.json`

The main orchestrator remains the sole authority for `HYDRATE_STATE`, state-file mutation, and `AGENTS.md` mutation.

## Delegated Step 2

This repo now also includes a dedicated Step-2 custom agent profile at
`.github/agents/metaopt-hydrate-state.agent.md`.

That agent wraps `scripts/hydrate_state_handoff.py`, which:
- consumes `.ml-metaopt/handoffs/load_campaign.latest.json`
- resumes or initializes authoritative `.ml-metaopt/state.json`
- manages the `AGENTS.md` resume hook
- verifies worker-target availability via `agents/worker-skills.json`
- writes `.ml-metaopt/handoffs/hydrate_state.latest.json`

After Step 2, the main orchestrator can continue from the hydrated machine state instead of redoing bootstrap/resume bookkeeping.

## Delegated Steps 3/4 Control

This repo now also includes a Steps 3/4 background-control custom agent at
`.github/agents/metaopt-background-control.agent.md`.

That agent wraps `scripts/background_control_handoff.py` and runs in two modes:
- `plan_background_work`
- `gate_background_work`

The orchestrator remains the only component that launches worker subagents, but the background-control agent now owns:
- lane assignment (`ideation` vs `maintenance`)
- staged task-file generation
- semantic integration of staged worker results
- proposal-pool and proposal-cycle updates
- threshold readiness decisions for `SELECT_EXPERIMENT`

The orchestrator is reduced to a transport/runtime shell in the middle:
- launch workers from task files
- persist raw worker outputs and slot events
- re-invoke the background-control agent

Step-3 ideation now uses the custom agent profile at
`.github/agents/metaopt-ideation-worker.agent.md` rather than the external
`metaopt-experiment-ideation` skill. The planner emits `worker_kind` /
`worker_ref` launch requests so the orchestrator can launch either a custom
agent (`metaopt-ideation-worker`) or a legacy skill
(`repo-audit-refactor-optimize`) without changing the Step-3/4 control flow.

## Delegated Steps 5/6

This repo now also includes a Step-5/6 control agent at
`.github/agents/metaopt-select-design.agent.md`.

That agent wraps `scripts/select_and_design_handoff.py` and now runs in three modes:
- `plan_select_experiment`
- `gate_select_and_plan_design`
- `finalize_select_design`

The Step-5/6 control agent owns:
- freezing the current proposal pool once selection begins
- staged selection-task and design-task generation
- validation of staged selection and design worker outputs
- canonical `state.selected_experiment` writes
- transition from `SELECT_EXPERIMENT` to `DESIGN_EXPERIMENT` to `MATERIALIZE_CHANGESET`

The orchestrator is reduced to the execution shell in the middle:
- launch `metaopt-selection-worker`
- stage its raw JSON result
- launch `metaopt-design-worker`
- stage its raw JSON result
- re-invoke the Step-5/6 control agent

After Step 5/6, the main orchestrator can continue directly to `MATERIALIZE_CHANGESET`.

## Delegated Steps 7/8 Control

This repo now also includes a Steps 7/8 local-execution control agent at
`.github/agents/metaopt-local-execution-control.agent.md`.

That agent wraps `scripts/local_execution_control_handoff.py` and runs in two modes:
- `plan_local_changeset`
- `gate_local_sanity`

The orchestrator remains the only component that launches materialization and diagnosis workers and runs `sanity.command`, but the local-execution control agent now owns:
- staged task generation for materialization attempts
- semantic integration of staged materialization outputs into `state.local_changeset`
- interpretation of staged local sanity results
- diagnosis-history and `sanity_attempts` updates
- retry routing to remediation, `BLOCKED_CONFIG`, `FAILED`, or `ENQUEUE_REMOTE_BATCH`

The orchestrator is reduced to a transport/runtime shell in the middle:
- launch workers from staged task files
- mechanically apply patches and package artifacts
- stage raw executor outputs
- run `sanity.command`
- re-invoke the local-execution control agent

Local and remote diagnosis now share the custom agent profile at
`.github/agents/metaopt-diagnosis-worker.agent.md`. The local and remote control
helpers emit `worker_kind` / `worker_ref` launch requests plus staged diagnosis
task files, and the orchestrator launches the same leaf diagnosis worker in both
paths.

Step-7 materialization now uses the custom agent profile at
`.github/agents/metaopt-materialization-worker.agent.md` rather than the external
`metaopt-experiment-materialization` skill. The planner emits `worker_kind` /
`worker_ref` launch requests so the orchestrator can launch the materialization
custom agent for `standard`, `remediation`, and `conflict_resolution` passes
without changing the surrounding Step-7/8 control flow.

## Delegated Steps 9/11 Control

This repo now also includes a Steps 9/11 remote-execution control agent at
`.github/agents/metaopt-remote-execution-control.agent.md`.

That agent wraps `scripts/remote_execution_control_handoff.py` and runs in three modes:
- `plan_remote_batch`
- `gate_remote_batch`
- `analyze_remote_results`

The orchestrator remains the only component that writes the immutable manifest, calls backend queue commands, and launches diagnosis or analysis workers, but the remote-execution control agent now owns:
- enqueue-readiness validation and deterministic `batch_id` planning
- semantic integration of staged enqueue and status payloads into `remote_batches`
- remote failure routing after staged diagnosis
- semantic interpretation of staged results-analysis output
- updates to `selected_experiment.analysis_summary`, `completed_experiments`, `baseline`, and `no_improve_iterations`

The orchestrator is reduced to a transport/runtime shell in the middle:
- write the manifest file
- run `enqueue_command`, `status_command`, and `results_command`
- stage raw backend stdout JSON
- launch remote diagnosis or analysis workers from staged task files
- re-invoke the remote-execution control agent

Remote results analysis now uses the custom agent profile at
`.github/agents/metaopt-analysis-worker.agent.md` rather than the external
`metaopt-results-analysis` skill. The remote-control helper emits
`worker_kind` / `worker_ref` launch requests and a self-sufficient staged
analysis task file before the orchestrator launches the analysis worker.

## Delegated Steps 12/13 Control

This repo now also includes a Steps 12/13 iteration-close control agent at
`.github/agents/metaopt-iteration-close-control.agent.md`.

That agent wraps `scripts/iteration_close_control_handoff.py` and runs in three modes:
- `plan_roll_iteration`
- `gate_roll_iteration`
- `quiesce_slots`

The orchestrator remains the only component that launches the rollover worker, drains or cancels slots, and performs terminal cleanup side effects, but the iteration-close control agent now owns:
- staged rollover task generation
- semantic integration of rollover output into the next proposal pool
- `selected_experiment` closure and iteration-report generation
- stop-condition evaluation
- continue-vs-complete routing after staged quiesce results

The orchestrator is reduced to a transport/runtime shell in the middle:
- launch the rollover worker from the staged task file
- stage raw quiesce outcomes after drain or cancel work
- perform final cleanup side effects only after the iteration-close control agent returns the terminal decision

Iteration rollover now uses the custom agent profile at
`.github/agents/metaopt-rollover-worker.agent.md` rather than the external
`metaopt-proposal-rollover` skill. The iteration-close helper emits
`worker_kind` / `worker_ref` launch requests and a self-sufficient staged
rollover task file before the orchestrator launches the rollover worker.

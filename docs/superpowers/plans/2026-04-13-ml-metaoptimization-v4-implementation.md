# ml-metaoptimization v4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the ml-metaoptimization skill from a Hetzner/Ray custom backend to SkyPilot + WandB + Vast.ai, narrowing scope to ML training campaigns exclusively.

**Architecture:** Agents propose WandB sweep configs (parameter distributions + search method). WandB Sweeps execute the search on SkyPilot-provisioned Vast.ai instances. The skill tracks campaign state, learnings, and iteration logic. No code patches, no slot accounting, no custom queue scripts.

**Tech Stack:** Python 3.11+ (scripts, tests), YAML (campaign spec), Markdown (agent + reference files), WandB Python API, SkyPilot CLI, Vast.ai (via SkyPilot provider).

**Spec:** `docs/superpowers/specs/2026-04-13-ml-metaoptimization-v4-design.md`

---

## File Map

### Python — modify
- `scripts/_guardrail_utils.py` — update worker allowlist, directive action set, remove v3 slot modes
- `scripts/_handoff_utils.py` — update state-patch ownership map, remove v3 slot helpers
- `scripts/remote_execution_control_handoff.py` — update for v4 operations (launch_sweep, poll_sweep, analyze)

### Python — delete
- `scripts/local_execution_control_handoff.py`

### Test fixtures — replace
- `tests/fixtures/state/running.json` — v4 schema (has `current_sweep`, no `active_slots`)
- `tests/fixtures/state/complete.json` — v4 schema
- `tests/fixtures/backend/launch-sweep-valid.json` — new
- `tests/fixtures/backend/poll-sweep-running.json` — new
- `tests/fixtures/backend/poll-sweep-completed.json` — new
- `tests/fixtures/backend/poll-sweep-budget-exceeded.json` — new

### Test fixtures — delete
- `tests/fixtures/backend/enqueue-*.json`
- `tests/fixtures/backend/status-*.json`
- `tests/fixtures/backend/results-*.json`
- `tests/fixtures/manifest/` (entire directory)
- `tests/fixtures/state/invalid-missing-local-changeset-metadata.json`
- `tests/fixtures/state/invalid-missing-slot-model-resolution.json`

### Test files — modify
- `tests/test_metaopt_validation.py` — update state set, remove slot/patch/manifest validation
- `tests/test_guardrail_utils.py` — update for v4 workers, directive actions

### Test files — delete
- `tests/test_local_execution_control_agent.py`

### Reference files — rewrite all
- `references/contracts.md`
- `references/backend-contract.md`
- `references/control-protocol.md`
- `references/state-machine.md`
- `references/worker-lanes.md`
- `references/dispatch-guide.md`
- `references/dependencies.md`

### Main skill — rewrite
- `SKILL.md`

### Agent files — rewrite
- `.github/agents/metaopt-load-campaign.agent.md`
- `.github/agents/metaopt-hydrate-state.agent.md`
- `.github/agents/metaopt-background-control.agent.md`
- `.github/agents/metaopt-ideation-worker.agent.md`
- `.github/agents/metaopt-select-design.agent.md` (merged select + design)
- `.github/agents/metaopt-remote-execution-control.agent.md`
- `.github/agents/metaopt-iteration-close-control.agent.md`
- `.github/agents/metaopt-analysis-worker.agent.md`
- `.github/agents/skypilot-wandb-worker.agent.md` (new)

### Agent files — delete
- `.github/agents/hetzner-delegation-worker.agent.md`
- `.github/agents/metaopt-design-worker.agent.md`
- `.github/agents/metaopt-materialization-worker.agent.md`
- `.github/agents/metaopt-diagnosis-worker.agent.md`
- `.github/agents/metaopt-rollover-worker.agent.md`
- `.github/agents/metaopt-local-execution-control.agent.md`

### Campaign example — rewrite
- `ml_metaopt_campaign.example.yaml`

---

## Task 1: Update `_guardrail_utils.py`

**Files:**
- Modify: `scripts/_guardrail_utils.py`

- [ ] **Step 1: Write the updated `_guardrail_utils.py`**

```python
"""Shared guardrail validators for launch requests and executor directives."""
from __future__ import annotations

from typing import Any


# --- Allowed slot modes per slot class ---
# v4: only ideation (background) and analysis (auxiliary) remain.
# All other execution is via directives to skypilot-wandb-worker.
ALLOWED_SLOT_MODES: dict[str, frozenset[str]] = {
    "background": frozenset({"ideation"}),
    "auxiliary": frozenset({"analysis"}),
}

# --- Allowed workers (worker_ref values) ---
ALLOWED_WORKERS: frozenset[str] = frozenset({
    "metaopt-ideation-worker",
    "metaopt-analysis-worker",
    "skypilot-wandb-worker",
})

# --- Deterministic model resolution order per class ---
MODEL_RESOLUTION_ORDER_BY_CLASS: dict[str, tuple[str, ...]] = {
    "general_worker": ("claude-sonnet-4", "gpt-5.4"),
    "strong_reasoner": ("claude-opus-4.6", "gpt-5.4"),
    "strong_coder": ("claude-opus-4.6", "gpt-5.4"),
}

# --- Preferred model per model class ---
PREFERRED_MODEL_BY_CLASS: dict[str, str] = {
    model_class: resolution_order[0]
    for model_class, resolution_order in MODEL_RESOLUTION_ORDER_BY_CLASS.items()
}

# --- Worker dispatch contract ---
# skypilot-wandb-worker has no slot_class — it is dispatched via directive, not launch_requests.
WORKER_DISPATCH_POLICY: dict[str, dict[str, Any]] = {
    "metaopt-ideation-worker": {
        "slot_class": "background",
        "modes": frozenset({"ideation"}),
        "model_classes": frozenset({"general_worker"}),
    },
    "metaopt-analysis-worker": {
        "slot_class": "auxiliary",
        "modes": frozenset({"analysis"}),
        "model_classes": frozenset({"strong_reasoner"}),
    },
    "skypilot-wandb-worker": {
        "slot_class": None,  # directive-dispatched only
        "modes": frozenset(),
        "model_classes": frozenset({"general_worker"}),
    },
}

# --- Allowed directive actions ---
# Blocked: queue_op, apply_patch_artifacts, package_code_artifact,
#          package_data_manifest, write_manifest, run_sanity,
#          drain_slots, cancel_slots, ssh_command, raw_ssh, kubectl_exec
ALLOWED_DIRECTIVE_ACTIONS: frozenset[str] = frozenset({
    "launch_sweep",
    "poll_sweep",
    "run_smoke_test",
    "remove_agents_hook",
    "delete_state_file",
    "emit_final_report",
    "emit_iteration_report",
    "none",
})

# --- Required fields per directive action ---
DIRECTIVE_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "launch_sweep": frozenset({"sweep_config", "sky_task_spec", "result_file"}),
    "poll_sweep": frozenset({"sweep_id", "sky_job_ids", "result_file"}),
    "run_smoke_test": frozenset({"command", "result_file"}),
    "remove_agents_hook": frozenset({"agents_path"}),
    "delete_state_file": frozenset({"state_path"}),
    "emit_final_report": frozenset({"report_type"}),
    "emit_iteration_report": frozenset({"report_type", "iteration"}),
    "none": frozenset(),
}

# --- Blocked actions (raw cluster bypass) ---
BLOCKED_DIRECTIVE_ACTIONS: frozenset[str] = frozenset({
    "ssh_command",
    "raw_ssh",
    "kubectl_exec",
    "queue_op",
    "apply_patch_artifacts",
    "package_code_artifact",
    "package_data_manifest",
    "write_manifest",
    "run_sanity",
    "drain_slots",
    "cancel_slots",
})

_LAUNCH_REQUEST_REQUIRED_FIELDS = frozenset({
    "worker_ref",
    "model_class",
    "task_file",
    "result_file",
})
_SLOT_REQUIRED_FIELDS = frozenset({"slot_id", "slot_class", "mode"})


def normalize_launch_requests(launch_requests: Any) -> list[dict[str, Any]]:
    """Validate and normalize a launch_requests list from a control-agent handoff.

    Adds ``preferred_model`` to each entry if not already present.
    Raises TypeError for structural problems, ValueError for semantic violations.
    """
    if launch_requests is None:
        return []
    if not isinstance(launch_requests, list):
        raise TypeError(f"launch_requests must be a list, got {type(launch_requests).__name__}")

    result = []
    for entry in launch_requests:
        if not isinstance(entry, dict):
            raise TypeError(f"each launch_requests entry must be a dict, got {type(entry).__name__}")

        entry = dict(entry)

        # Validate required base fields
        missing = _LAUNCH_REQUEST_REQUIRED_FIELDS - entry.keys()
        if missing:
            raise ValueError(f"launch_requests entry missing required fields: {sorted(missing)}")

        worker_ref = entry["worker_ref"]
        if worker_ref not in ALLOWED_WORKERS:
            raise ValueError(
                f"unknown worker_ref {worker_ref!r}; allowed: {sorted(ALLOWED_WORKERS)}"
            )

        model_class = entry["model_class"]
        if model_class not in MODEL_RESOLUTION_ORDER_BY_CLASS:
            raise ValueError(
                f"unknown model_class {model_class!r}; allowed: "
                f"{sorted(MODEL_RESOLUTION_ORDER_BY_CLASS)}"
            )

        policy = WORKER_DISPATCH_POLICY[worker_ref]

        has_slot_class = "slot_class" in entry
        has_mode = "mode" in entry

        if has_slot_class != has_mode:
            if not has_slot_class:
                raise ValueError("slot_class is required when mode is present")
            raise ValueError("mode is required when slot_class is present")

        if has_slot_class:
            slot_class = entry["slot_class"]
            mode = entry["mode"]

            if slot_class not in ALLOWED_SLOT_MODES:
                raise ValueError(
                    f"unknown slot_class {slot_class!r}; allowed: {sorted(ALLOWED_SLOT_MODES)}"
                )

            if policy["slot_class"] is None:
                raise ValueError(
                    f"worker {worker_ref!r} is directive-dispatched and must not appear in "
                    "slot-based launch_requests"
                )

            if slot_class != policy["slot_class"]:
                raise ValueError(
                    f"worker {worker_ref!r} requires slot_class {policy['slot_class']!r}, "
                    f"got {slot_class!r}"
                )

            allowed_modes = ALLOWED_SLOT_MODES[slot_class]
            if mode not in allowed_modes:
                raise ValueError(
                    f"slot_class {slot_class!r} does not allow mode {mode!r}; "
                    f"allowed: {sorted(allowed_modes)}"
                )

            if mode not in policy["modes"]:
                raise ValueError(
                    f"worker {worker_ref!r} requires one of modes {sorted(policy['modes'])}, "
                    f"got {mode!r}"
                )

            if model_class not in policy["model_classes"]:
                raise ValueError(
                    f"worker {worker_ref!r} requires model classes "
                    f"{sorted(policy['model_classes'])}, got {model_class!r}"
                )

        # Inject preferred_model if not set
        if "preferred_model" not in entry:
            entry["preferred_model"] = PREFERRED_MODEL_BY_CLASS[model_class]

        result.append(entry)

    return result


def validate_executor_policy(
    agent_name: str,
    phase: str | None,
    directives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate a list of executor directives from a control-agent handoff.

    Raises ValueError for blocked actions or schema violations.
    Returns the validated directive list unchanged on success.
    """
    for directive in directives:
        action = directive.get("action", "")

        if action in BLOCKED_DIRECTIVE_ACTIONS:
            raise ValueError(
                f"directive action {action!r} is blocked (raw-cluster bypass or v3 artifact "
                f"operation); agent={agent_name!r} phase={phase!r}"
            )

        if action not in ALLOWED_DIRECTIVE_ACTIONS:
            raise ValueError(
                f"unknown directive action {action!r}; allowed: {sorted(ALLOWED_DIRECTIVE_ACTIONS)}"
            )

        required = DIRECTIVE_REQUIRED_FIELDS.get(action, frozenset())
        missing = required - directive.keys()
        if missing:
            raise ValueError(
                f"directive {action!r} missing required fields: {sorted(missing)}"
            )

    return directives
```

- [ ] **Step 2: Run the existing guardrail tests to confirm they fail with new expectations**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/test_guardrail_utils.py -v 2>&1 | head -50
```

Expected: several failures (tests reference removed workers and directives — these will be fixed in Task 4).

- [ ] **Step 3: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add scripts/_guardrail_utils.py
git commit -m "refactor: update guardrail_utils for v4 — WandB/SkyPilot worker set and directive actions"
```

---

## Task 2: Replace state and backend test fixtures

**Files:**
- Replace: `tests/fixtures/state/running.json`
- Replace: `tests/fixtures/state/complete.json`
- Create: `tests/fixtures/backend/launch-sweep-valid.json`
- Create: `tests/fixtures/backend/poll-sweep-running.json`
- Create: `tests/fixtures/backend/poll-sweep-completed.json`
- Create: `tests/fixtures/backend/poll-sweep-budget-exceeded.json`
- Delete: `tests/fixtures/backend/enqueue-*.json`, `tests/fixtures/backend/status-*.json`, `tests/fixtures/backend/results-*.json`
- Delete: `tests/fixtures/manifest/`
- Delete: `tests/fixtures/state/invalid-missing-local-changeset-metadata.json`, `tests/fixtures/state/invalid-missing-slot-model-resolution.json`

- [ ] **Step 1: Write the v4 running state fixture**

Write `tests/fixtures/state/running.json`:

```json
{
  "version": 4,
  "campaign_id": "gnn-mnist-v4",
  "campaign_identity_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "status": "RUNNING",
  "machine_state": "WAIT_FOR_SWEEP",
  "current_iteration": 2,
  "next_action": "poll WandB sweep status",
  "objective_snapshot": {
    "metric": "val/accuracy",
    "direction": "maximize",
    "improvement_threshold": 0.005
  },
  "proposal_cycle": {
    "cycle_id": "iter-2-cycle-1",
    "current_pool_frozen": true
  },
  "current_sweep": {
    "sweep_id": "wandb-sweep-abc123",
    "sweep_url": "https://wandb.ai/my-entity/my-project/sweeps/abc123",
    "sky_job_ids": ["sky-job-001", "sky-job-002"],
    "launched_at": "2026-04-13T10:00:00Z",
    "cumulative_spend_usd": 2.40
  },
  "selected_sweep": {
    "proposal_id": "prop-003",
    "sweep_config": {
      "method": "bayes",
      "metric": {"name": "val/accuracy", "goal": "maximize"},
      "parameters": {
        "lr": {"distribution": "log_uniform_values", "min": 1e-4, "max": 1e-2},
        "num_layers": {"values": [2, 3, 4]}
      }
    }
  },
  "baseline": {
    "metric": "val/accuracy",
    "value": 0.923,
    "wandb_run_id": "run-baseline-001",
    "wandb_run_url": "https://wandb.ai/my-entity/my-project/runs/run-baseline-001",
    "established_at": "2026-04-13T08:00:00Z"
  },
  "current_proposals": [],
  "next_proposals": [
    {
      "proposal_id": "prop-004",
      "rationale": "Try attention pooling based on prior learnings",
      "sweep_config": {
        "method": "bayes",
        "metric": {"name": "val/accuracy", "goal": "maximize"},
        "parameters": {
          "pool_type": {"values": ["mean", "attention"]},
          "hidden_dim": {"values": [64, 128, 256]}
        }
      }
    }
  ],
  "key_learnings": [
    "Deeper networks (num_layers=4) overfit on MNIST without residual connections"
  ],
  "completed_iterations": [
    {
      "iteration": 1,
      "sweep_id": "wandb-sweep-prev001",
      "best_metric_value": 0.923,
      "spend_usd": 3.10,
      "improved_baseline": true
    }
  ],
  "no_improve_iterations": 0,
  "campaign_started_at": "2026-04-13T07:00:00Z"
}
```

- [ ] **Step 2: Write the v4 complete state fixture**

Write `tests/fixtures/state/complete.json`:

```json
{
  "version": 4,
  "campaign_id": "gnn-mnist-v4",
  "campaign_identity_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "status": "COMPLETE",
  "machine_state": "COMPLETE",
  "current_iteration": 5,
  "next_action": null,
  "objective_snapshot": {
    "metric": "val/accuracy",
    "direction": "maximize",
    "improvement_threshold": 0.005
  },
  "proposal_cycle": {
    "cycle_id": "iter-5-cycle-1",
    "current_pool_frozen": true
  },
  "current_sweep": null,
  "selected_sweep": null,
  "baseline": {
    "metric": "val/accuracy",
    "value": 0.971,
    "wandb_run_id": "run-best-042",
    "wandb_run_url": "https://wandb.ai/my-entity/my-project/runs/run-best-042",
    "established_at": "2026-04-13T18:00:00Z"
  },
  "current_proposals": [],
  "next_proposals": [],
  "key_learnings": [
    "Residual connections critical for num_layers >= 3",
    "Bayes search finds optimal lr in 8-12 runs"
  ],
  "completed_iterations": [],
  "no_improve_iterations": 2,
  "campaign_started_at": "2026-04-13T07:00:00Z"
}
```

- [ ] **Step 3: Write the backend fixtures**

Write `tests/fixtures/backend/launch-sweep-valid.json`:

```json
{
  "operation": "launch_sweep",
  "exit_code": 0,
  "sweep_id": "wandb-sweep-abc123",
  "sweep_url": "https://wandb.ai/my-entity/my-project/sweeps/abc123",
  "sky_job_ids": ["sky-job-001", "sky-job-002", "sky-job-003", "sky-job-004"],
  "launched_at": "2026-04-13T10:00:00Z"
}
```

Write `tests/fixtures/backend/poll-sweep-running.json`:

```json
{
  "operation": "poll_sweep",
  "exit_code": 0,
  "sweep_status": "running",
  "best_metric_value": 0.941,
  "best_run_id": "run-iter2-007",
  "killed_runs": [],
  "cumulative_spend_usd": 2.40
}
```

Write `tests/fixtures/backend/poll-sweep-completed.json`:

```json
{
  "operation": "poll_sweep",
  "exit_code": 0,
  "sweep_status": "completed",
  "best_metric_value": 0.957,
  "best_run_id": "run-iter2-019",
  "killed_runs": ["run-iter2-003"],
  "cumulative_spend_usd": 6.80
}
```

Write `tests/fixtures/backend/poll-sweep-budget-exceeded.json`:

```json
{
  "operation": "poll_sweep",
  "exit_code": 0,
  "sweep_status": "budget_exceeded",
  "best_metric_value": 0.944,
  "best_run_id": "run-iter2-011",
  "killed_runs": ["run-iter2-012", "run-iter2-013"],
  "cumulative_spend_usd": 10.00
}
```

- [ ] **Step 4: Delete obsolete fixtures**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
rm tests/fixtures/backend/enqueue-invalid-missing-batch-id.json
rm tests/fixtures/backend/enqueue-valid.json
rm tests/fixtures/backend/status-valid.json
rm tests/fixtures/backend/status-invalid-lifecycle.json
rm tests/fixtures/backend/results-valid.json
rm -rf tests/fixtures/manifest/
rm tests/fixtures/state/invalid-missing-local-changeset-metadata.json
rm tests/fixtures/state/invalid-missing-slot-model-resolution.json
```

- [ ] **Step 5: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add tests/fixtures/
git commit -m "test: replace v3 fixtures with v4 state and backend fixtures"
```

---

## Task 3: Update `test_metaopt_validation.py`

**Files:**
- Modify: `tests/test_metaopt_validation.py`

- [ ] **Step 1: Update the machine state set and remove v3-specific validation**

At the top of `tests/test_metaopt_validation.py`, replace the constants block (lines 12–58) with:

```python
ROOT = Path(__file__).resolve().parents[1]

VALID_MACHINE_STATES = {
    "LOAD_CAMPAIGN",
    "HYDRATE_STATE",
    "IDEATE",
    "WAIT_FOR_PROPOSALS",
    "SELECT_AND_DESIGN_SWEEP",
    "LOCAL_SANITY",
    "LAUNCH_SWEEP",
    "WAIT_FOR_SWEEP",
    "ANALYZE",
    "ROLL_ITERATION",
    "COMPLETE",
    "BLOCKED_CONFIG",
    "BLOCKED_PROTOCOL",
    "FAILED",
}
TERMINAL_MACHINE_STATES = {"COMPLETE", "BLOCKED_CONFIG", "BLOCKED_PROTOCOL", "FAILED"}
VALID_STATE_STATUSES = {"RUNNING", "BLOCKED_CONFIG", "BLOCKED_PROTOCOL", "FAILED", "COMPLETE"}
VALID_SWEEP_STATUSES = {"running", "completed", "failed", "budget_exceeded"}
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
```

- [ ] **Step 2: Update state fixture validation tests**

Find all test methods that check for v3-only fields (`active_slots`, `local_changeset`, `proposal_cycle.ideation_rounds_by_slot`, `remote_batches`) and either remove them or replace with v4 equivalents:

- Replace assertions on `active_slots` with assertions on `current_sweep` (can be `None` in terminal states)
- Replace assertions on `local_changeset` with assertions on `selected_sweep` (can be `None`)
- Remove assertions on `remote_batches`, `proposal_cycle.ideation_rounds_by_slot`
- Add assertion: if `machine_state` in `TERMINAL_MACHINE_STATES`, then `current_sweep` must be `None`
- Add assertion: `state["version"]` must equal `4`

- [ ] **Step 3: Update campaign YAML validation**

Find the test that validates `ml_metaopt_campaign.example.yaml` and update required field checks:

Required top-level keys for v4: `campaign`, `project`, `wandb`, `compute`, `objective`, `proposal_policy`, `stop_conditions`

Remove checks for: `datasets`, `dispatch_policy`, `sanity` (replaced by `project.smoke_test_command`), `artifacts`, `remote_queue`, `execution`

Required `compute` keys: `provider`, `accelerator`, `num_sweep_agents`, `idle_timeout_minutes`, `max_budget_usd`

Required `wandb` keys: `entity`, `project`

Required `objective` keys: `metric`, `direction`, `improvement_threshold`

- [ ] **Step 4: Run the tests**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/test_metaopt_validation.py -v 2>&1 | tail -20
```

Expected: all tests pass (or only fail on tests that reference missing campaign example — fixed in Task 23).

- [ ] **Step 5: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add tests/test_metaopt_validation.py
git commit -m "test: update metaopt_validation for v4 state machine and campaign schema"
```

---

## Task 4: Update `test_guardrail_utils.py`

**Files:**
- Modify: `tests/test_guardrail_utils.py`

- [ ] **Step 1: Remove tests for deleted workers and modes**

Remove or update these test methods:
- `test_background_slot_rejects_materialization_mode` — materialization is gone; replace with a test that background slot rejects `analysis` mode
- `test_strong_coder_gets_preferred_model` — `metaopt-materialization-worker` is gone; replace with a test using `metaopt-analysis-worker` with `strong_reasoner`
- `test_allowed_workers_contains_known_agents` — update expected set
- `test_background_modes_do_not_include_materialization` — keep (still true)
- `test_auxiliary_slot_does_not_include_rollover_mode` — keep (still true, rollover absorbed)
- Remove: `test_rollover_launch_request_without_slot_class_is_accepted` — rollover worker deleted
- Remove: `test_rollover_launch_request_with_auxiliary_slot_is_rejected` — rollover worker deleted

- [ ] **Step 2: Update `ConstantsSanityTests`**

Replace `test_allowed_workers_contains_known_agents` with:

```python
def test_allowed_workers_contains_known_agents(self) -> None:
    expected = {
        "metaopt-ideation-worker",
        "metaopt-analysis-worker",
        "skypilot-wandb-worker",
    }
    self.assertEqual(ALLOWED_WORKERS, expected)
```

Add a test for the new directive actions:

```python
def test_allowed_directive_actions_contains_v4_actions(self) -> None:
    expected = {
        "launch_sweep", "poll_sweep", "run_smoke_test",
        "remove_agents_hook", "delete_state_file",
        "emit_final_report", "emit_iteration_report", "none",
    }
    self.assertEqual(ALLOWED_DIRECTIVE_ACTIONS, expected)
```

- [ ] **Step 3: Add tests for new v4 directive validation**

```python
class ValidateExecutorPolicyV4Tests(unittest.TestCase):

    def test_launch_sweep_passes(self) -> None:
        directives = [{
            "action": "launch_sweep",
            "reason": "launch WandB sweep on Vast.ai",
            "sweep_config": {"method": "bayes", "parameters": {}},
            "sky_task_spec": {"accelerator": "A100:1"},
            "result_file": ".ml-metaopt/worker-results/launch-sweep-iter-2.json",
        }]
        result = validate_executor_policy("metaopt-remote-execution-control", "LAUNCH_SWEEP", directives)
        self.assertEqual(len(result), 1)

    def test_poll_sweep_passes(self) -> None:
        directives = [{
            "action": "poll_sweep",
            "reason": "check sweep status and watchdog",
            "sweep_id": "wandb-sweep-abc123",
            "sky_job_ids": ["sky-job-001"],
            "result_file": ".ml-metaopt/worker-results/poll-sweep-iter-2.json",
        }]
        result = validate_executor_policy("metaopt-remote-execution-control", "WAIT_FOR_SWEEP", directives)
        self.assertEqual(len(result), 1)

    def test_run_smoke_test_passes(self) -> None:
        directives = [{
            "action": "run_smoke_test",
            "reason": "60s crash-detection gate before GPU spend",
            "command": "python train.py --smoke",
            "result_file": ".ml-metaopt/worker-results/smoke-test-iter-2.json",
        }]
        result = validate_executor_policy("metaopt-remote-execution-control", "LOCAL_SANITY", directives)
        self.assertEqual(len(result), 1)

    def test_queue_op_blocked(self) -> None:
        directives = [{"action": "queue_op", "reason": "v3 compat"}]
        with self.assertRaises(ValueError):
            validate_executor_policy("any-agent", "SOME_PHASE", directives)

    def test_apply_patch_artifacts_blocked(self) -> None:
        directives = [{"action": "apply_patch_artifacts", "reason": "v3 compat"}]
        with self.assertRaises(ValueError):
            validate_executor_policy("any-agent", "SOME_PHASE", directives)

    def test_skypilot_worker_rejected_in_slot_launch_requests(self) -> None:
        request = {
            "slot_id": "aux-1",
            "slot_class": "auxiliary",
            "mode": "analysis",
            "worker_ref": "skypilot-wandb-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/sky.md",
            "result_file": ".ml-metaopt/worker-results/sky.json",
        }
        with self.assertRaises(ValueError, msg="skypilot-wandb-worker must not appear in slot-based launch_requests"):
            normalize_launch_requests([request])

    def test_analysis_worker_in_auxiliary_slot_accepted(self) -> None:
        request = {
            "slot_id": "aux-1",
            "slot_class": "auxiliary",
            "mode": "analysis",
            "worker_ref": "metaopt-analysis-worker",
            "model_class": "strong_reasoner",
            "task_file": ".ml-metaopt/tasks/analysis-iter-2.md",
            "result_file": ".ml-metaopt/worker-results/analysis-iter-2.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["preferred_model"], "claude-opus-4.6")
```

- [ ] **Step 4: Run all guardrail tests**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/test_guardrail_utils.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add tests/test_guardrail_utils.py
git commit -m "test: update guardrail tests for v4 workers, modes, and directive actions"
```

---

## Task 5: Update `_handoff_utils.py`

**Files:**
- Modify: `scripts/_handoff_utils.py`

- [ ] **Step 1: Update `STATE_PATCH_OWNERSHIP`**

Replace the `STATE_PATCH_OWNERSHIP` dict with the v4 ownership map. Read the current file first to find the exact lines, then replace the entire dict:

```python
STATE_PATCH_OWNERSHIP: dict[str, tuple[tuple[str, ...], ...]] = {
    "metaopt-load-campaign": tuple(),
    "metaopt-hydrate-state": (
        ("version",),
        ("campaign_id",),
        ("campaign_identity_hash",),
        ("current_iteration",),
        ("next_action",),
        ("objective_snapshot",),
        ("proposal_cycle",),
        ("current_proposals",),
        ("next_proposals",),
        ("selected_sweep",),
        ("current_sweep",),
        ("baseline",),
        ("completed_iterations",),
        ("key_learnings",),
        ("no_improve_iterations",),
        ("campaign_started_at",),
    ),
    "metaopt-background-control": (
        ("proposal_cycle",),
        ("current_proposals",),
        ("next_proposals",),
        ("next_action",),
    ),
    "metaopt-select-design": (
        ("selected_sweep",),
        ("next_action",),
    ),
    "metaopt-remote-execution-control": (
        ("current_sweep",),
        ("baseline",),
        ("completed_iterations",),
        ("key_learnings",),
        ("no_improve_iterations",),
        ("next_action",),
    ),
    "metaopt-iteration-close-control": (
        ("current_iteration",),
        ("no_improve_iterations",),
        ("next_proposals",),
        ("current_proposals",),
        ("completed_iterations",),
        ("proposal_cycle",),
        ("next_action",),
    ),
}
```

- [ ] **Step 2: Remove v3-specific helper functions**

Remove any helper functions that reference: `active_slots`, `launch_requests`, `pre_launch_directives`, `post_launch_directives`, slot accounting.

Keep helpers that are still relevant: `TERMINAL_MACHINE_STATES`, timestamp utilities, handoff envelope construction.

- [ ] **Step 3: Update `TERMINAL_MACHINE_STATES` and `LEGACY_HANDOFF_FIELDS`**

The `TERMINAL_MACHINE_STATES` set stays the same. Update `LEGACY_HANDOFF_FIELDS` to include all v3 fields:

```python
LEGACY_HANDOFF_FIELDS = frozenset({
    "producer",
    "phase",
    "outcome",
    "recommended_next_action",
    "recommended_executor_phase",
    "executor_directives",
    "pre_launch_directives",
    "post_launch_directives",
    "launch_requests",
})
```

- [ ] **Step 4: Run affected tests**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/test_handoff_utils.py -v
```

Fix any failures due to removed functions.

- [ ] **Step 5: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add scripts/_handoff_utils.py
git commit -m "refactor: update handoff_utils for v4 state-patch ownership"
```

---

## Task 6: Delete obsolete files

**Files:**
- Delete: `scripts/local_execution_control_handoff.py`
- Delete: `tests/test_local_execution_control_agent.py`
- Delete: `.github/agents/hetzner-delegation-worker.agent.md`
- Delete: `.github/agents/metaopt-design-worker.agent.md`
- Delete: `.github/agents/metaopt-materialization-worker.agent.md`
- Delete: `.github/agents/metaopt-diagnosis-worker.agent.md`
- Delete: `.github/agents/metaopt-rollover-worker.agent.md`
- Delete: `.github/agents/metaopt-local-execution-control.agent.md`

- [ ] **Step 1: Delete all obsolete files**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
rm scripts/local_execution_control_handoff.py
rm tests/test_local_execution_control_agent.py
rm .github/agents/hetzner-delegation-worker.agent.md
rm .github/agents/metaopt-design-worker.agent.md
rm .github/agents/metaopt-materialization-worker.agent.md
rm .github/agents/metaopt-diagnosis-worker.agent.md
rm .github/agents/metaopt-rollover-worker.agent.md
rm .github/agents/metaopt-local-execution-control.agent.md
```

- [ ] **Step 2: Confirm full test suite still runs**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/ -v --ignore=tests/test_metaopt_validation.py 2>&1 | tail -20
```

Expected: remaining tests pass (metaopt_validation may still fail until campaign example is updated in Task 23).

- [ ] **Step 3: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add -u
git commit -m "chore: delete v3 agents, scripts, and tests — hetzner, materialization, diagnosis, rollover, local-execution-control"
```

---

## Task 7: Write `references/contracts.md`

**Files:**
- Rewrite: `references/contracts.md`

- [ ] **Step 1: Write the contracts reference**

Write `references/contracts.md` with these sections:

**Section 1 — State file schema (`state.json`)**

Define all v4 state fields with types and constraints:

```
version: integer, must be 4
campaign_id: string, non-empty
campaign_identity_hash: string, sha256:<64 hex chars>
status: one of RUNNING | BLOCKED_CONFIG | BLOCKED_PROTOCOL | FAILED | COMPLETE
machine_state: one of <VALID_MACHINE_STATES>
current_iteration: integer >= 0
next_action: string | null
objective_snapshot:
  metric: string, non-empty (e.g. "val/accuracy")
  direction: "maximize" | "minimize"
  improvement_threshold: float > 0
proposal_cycle:
  cycle_id: string
  current_pool_frozen: boolean
current_sweep: null | object with:
  sweep_id: string
  sweep_url: string
  sky_job_ids: list[string]
  launched_at: ISO 8601 timestamp
  cumulative_spend_usd: float >= 0
selected_sweep: null | object with:
  proposal_id: string
  sweep_config: WandB sweep config object (method, metric, parameters)
baseline: null | object with:
  metric: string
  value: float
  wandb_run_id: string
  wandb_run_url: string
  established_at: ISO 8601 timestamp
current_proposals: list[proposal]
next_proposals: list[proposal]
key_learnings: list[string]
completed_iterations: list[iteration_record]
no_improve_iterations: integer >= 0
campaign_started_at: ISO 8601 timestamp
```

A `proposal` object:
```
proposal_id: string
rationale: string
sweep_config: WandB sweep config (method, metric, parameters)
```

An `iteration_record` object:
```
iteration: integer
sweep_id: string
best_metric_value: float
spend_usd: float
improved_baseline: boolean
```

**Section 2 — Handoff envelope schema**

Every control-agent handoff must be a JSON object written to `.ml-metaopt/handoffs/<agent>-<state>.json`:

```
recommended_next_machine_state: string | null
  null means the gate phase ran but no state transition yet (poll again)
  non-null must be a valid machine state
state_patch: object
  keys must match STATE_PATCH_OWNERSHIP for this agent
directive: object with:
  type: one of ALLOWED_DIRECTIVE_ACTIONS
  payload: object (action-specific fields)
```

**Section 3 — Worker result file schema**

All worker and directive results are written to `.ml-metaopt/worker-results/<name>.json`. No other output path is valid.

**Section 4 — Identity hash computation**

`campaign_identity_hash`: SHA-256 of the canonical JSON of the `campaign` + `project` + `wandb` + `objective` fields from the campaign YAML (sorted keys, no whitespace).

**Section 5 — Baseline comparison**

Direction-aware: for `maximize`, new value beats baseline if `new > baseline + improvement_threshold`. For `minimize`, if `new < baseline - improvement_threshold`.

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add references/contracts.md
git commit -m "docs: rewrite contracts.md for v4 state schema and handoff envelope"
```

---

## Task 8: Write `references/backend-contract.md`

**Files:**
- Rewrite: `references/backend-contract.md`

- [ ] **Step 1: Write the backend contract**

Write `references/backend-contract.md` with:

**Purpose:** All remote execution goes through `skypilot-wandb-worker`. The skill never calls SkyPilot or WandB APIs directly — only the worker does.

**Three operations (directive types → worker operations):**

`launch_sweep` directive → worker calls:
1. WandB API: `wandb.sweep(sweep_config, project=..., entity=...)` → returns `sweep_id`
2. `sky launch --idle-minutes-to-autostop <idle_timeout_minutes>` for each of `num_sweep_agents` agents, each running `wandb agent <entity>/<project>/<sweep_id>`
3. Returns: `{ "sweep_id", "sweep_url", "sky_job_ids", "launched_at" }` written to `result_file`

`poll_sweep` directive → worker:
1. Queries WandB API for sweep status and best run
2. For each active run: checks `last_log_at`. If `now - last_log_at > idle_timeout_minutes`, calls `sky down <job_id>` and marks the run crashed via WandB API
3. Queries cumulative cost via SkyPilot. If `>= max_budget_usd`, kills all remaining jobs, returns `budget_exceeded`
4. Returns: `{ "sweep_status", "best_metric_value", "best_run_id", "killed_runs", "cumulative_spend_usd" }` written to `result_file`

`run_smoke_test` directive → worker:
1. Runs `command` locally (or on a cheap CPU instance if `project.repo` is a remote URL)
2. Enforces 60-second hard timeout
3. Returns: `{ "exit_code", "timed_out": boolean, "stdout_tail", "stderr_tail" }` written to `result_file`

**Forbidden:** raw SSH, direct Vast.ai API, `sky exec`, `ray job submit`. Any operation not listed above is a protocol breach → `BLOCKED_PROTOCOL`.

**Instance lifecycle contract:**
- Every `sky launch` includes `--idle-minutes-to-autostop <idle_timeout_minutes>`
- Instances self-terminate if the skill crashes mid-session
- On resume, `poll_sweep` reconnects to the existing sweep via `sweep_id` in state

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add references/backend-contract.md
git commit -m "docs: rewrite backend-contract.md for v4 SkyPilot+WandB operations"
```

---

## Task 9: Write `references/control-protocol.md`

**Files:**
- Rewrite: `references/control-protocol.md`

- [ ] **Step 1: Write the control protocol**

Write `references/control-protocol.md` with:

**Core rule:** The orchestrator invokes the governing control agent, reads the handoff JSON it writes to `.ml-metaopt/handoffs/`, and executes exactly one directive mechanically.

**Handoff file naming:** `.ml-metaopt/handoffs/<agent-name>-<machine_state>.json`

**Handoff envelope (full schema):**
```json
{
  "recommended_next_machine_state": "LAUNCH_SWEEP",
  "state_patch": { "<field>": "<value>" },
  "directive": {
    "type": "launch_sweep | poll_sweep | run_smoke_test | remove_agents_hook | delete_state_file | emit_final_report | emit_iteration_report | none",
    "payload": {}
  }
}
```

**Orchestrator execution sequence (per reinvocation):**
1. Read `.ml-metaopt/state.json` to determine `machine_state`
2. Determine phase: if latest handoff for current state has `recommended_next_machine_state = null`, invoke gate phase; else invoke plan phase (or single-phase for states with one phase)
3. Invoke governing control agent as subagent; wait for handoff file
4. Validate handoff: check state-patch ownership, directive action allowlist
5. Execute directive
6. Apply `state_patch` to state.json
7. Set `machine_state` and `status` from `recommended_next_machine_state`
8. Persist state

**Phase conventions:**
- `LOAD_CAMPAIGN`, `HYDRATE_STATE`: single phase (no plan/gate split)
- `IDEATE` / `WAIT_FOR_PROPOSALS`: `plan_ideation` → `gate_ideation`
- `SELECT_AND_DESIGN_SWEEP`: `plan_select_design` → `finalize_select_design`
- `LOCAL_SANITY`: single phase (emit directive, read result, advance or fail)
- `LAUNCH_SWEEP`: single phase
- `WAIT_FOR_SWEEP`: `poll` (loops until `recommended_next_machine_state` is non-null)
- `ANALYZE`: single phase
- `ROLL_ITERATION`: single phase

**null `recommended_next_machine_state`:** The orchestrator stays in the current state and re-invokes the control agent on the next session. Used during `WAIT_FOR_SWEEP` polling.

**State-patch validation:** The orchestrator rejects any `state_patch` key not in `STATE_PATCH_OWNERSHIP` for the invoking agent (from `_handoff_utils.py`). On rejection → `BLOCKED_PROTOCOL`.

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add references/control-protocol.md
git commit -m "docs: rewrite control-protocol.md for v4 simplified handoff envelope"
```

---

## Task 10: Write `references/state-machine.md`

**Files:**
- Rewrite: `references/state-machine.md`

- [ ] **Step 1: Write the state machine reference**

Write `references/state-machine.md` with:

**State list:** LOAD_CAMPAIGN, HYDRATE_STATE, IDEATE, WAIT_FOR_PROPOSALS, SELECT_AND_DESIGN_SWEEP, LOCAL_SANITY, LAUNCH_SWEEP, WAIT_FOR_SWEEP, ANALYZE, ROLL_ITERATION, COMPLETE, BLOCKED_CONFIG, BLOCKED_PROTOCOL, FAILED

**Control agent dispatch table:**

| State(s) | Agent | Phase(s) |
|---|---|---|
| LOAD_CAMPAIGN | metaopt-load-campaign | single (validate) |
| HYDRATE_STATE | metaopt-hydrate-state | single (hydrate) |
| IDEATE, WAIT_FOR_PROPOSALS | metaopt-background-control | plan_ideation → gate_ideation |
| SELECT_AND_DESIGN_SWEEP | metaopt-select-design | plan_select_design → finalize_select_design |
| LOCAL_SANITY | metaopt-remote-execution-control | single (gate_local_sanity) |
| LAUNCH_SWEEP | metaopt-remote-execution-control | single (plan_launch) |
| WAIT_FOR_SWEEP | metaopt-remote-execution-control | poll (loops) |
| ANALYZE | metaopt-remote-execution-control | single (analyze) |
| ROLL_ITERATION | metaopt-iteration-close-control | single (roll) |

**State semantics** (one paragraph each, describing what the governing agent does):

- LOAD_CAMPAIGN: validate campaign YAML, check preflight artifact, compute identity hash
- HYDRATE_STATE: init or resume state, crash-recovery via `current_sweep.sweep_id`, verify `skypilot-wandb-worker` availability
- IDEATE: dispatch `metaopt-ideation-worker` background agents that produce sweep search space proposals
- WAIT_FOR_PROPOSALS: gate — require `proposal_policy.current_target` proposals before advancing
- SELECT_AND_DESIGN_SWEEP: pick best proposal, refine sweep config, freeze proposal pool
- LOCAL_SANITY: emit `run_smoke_test` directive (60s hard limit); if exit_code != 0 or timed_out → FAILED
- LAUNCH_SWEEP: emit `launch_sweep` directive; persist sweep_id and sky_job_ids to state
- WAIT_FOR_SWEEP: emit `poll_sweep` directive; watchdog kills hung agents; budget gate; repeat until completed/failed/budget_exceeded
- ANALYZE: dispatch `metaopt-analysis-worker` to read best WandB run, update baseline if improved
- ROLL_ITERATION: filter next_proposals, increment iteration, check stop conditions, emit iteration report

**Stop conditions** (checked in ROLL_ITERATION):
- `metric meets target_metric` (direction-aware) → COMPLETE
- `current_iteration >= max_iterations` → COMPLETE
- `no_improve_iterations >= max_no_improve_iterations` → COMPLETE
- `cumulative_spend_usd >= max_budget_usd` → BLOCKED_CONFIG

**Terminal state cleanup directives:**

| Terminal | Required directives |
|---|---|
| COMPLETE | remove_agents_hook, delete_state_file, emit_final_report |
| BLOCKED_CONFIG | remove_agents_hook |
| BLOCKED_PROTOCOL | remove_agents_hook |
| FAILED | remove_agents_hook |

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add references/state-machine.md
git commit -m "docs: rewrite state-machine.md for v4 states and dispatch table"
```

---

## Task 11: Write remaining reference files

**Files:**
- Rewrite: `references/worker-lanes.md`
- Rewrite: `references/dispatch-guide.md`
- Rewrite: `references/dependencies.md`

- [ ] **Step 1: Write `references/worker-lanes.md`**

Three lanes:

**Ideation lane** (`metaopt-ideation-worker`, slot_class=background, mode=ideation, model=general_worker):
- Input: campaign objective, prior learnings, baseline, completed iterations, rejected proposals
- Output: a JSON file with `proposal_id`, `rationale`, `sweep_config` (WandB format with `method`, `metric`, `parameters`)
- Constraint: sweep_config must be valid WandB sweep config; parameters must be within declared domains (no made-up metrics)
- Lane drift: must not produce code patches, file diffs, or architecture change instructions

**Analysis lane** (`metaopt-analysis-worker`, slot_class=auxiliary, mode=analysis, model=strong_reasoner):
- Input: WandB best run result (metrics, hyperparams, run URL), current baseline, prior learnings
- Output: `{ "improved": boolean, "new_baseline": {...} | null, "learnings": [string], "best_run_id": string }`
- Constraint: baseline update uses direction-aware comparison from contracts.md Section 5
- Lane drift: must not emit sweep configs or code changes

**Execution lane** (`skypilot-wandb-worker`, directive-dispatched only):
- Not a slot worker. Dispatched via `launch_sweep` and `poll_sweep` directives.
- See backend-contract.md for full contract.

- [ ] **Step 2: Write `references/dispatch-guide.md`**

Per-state dispatch rules:

- IDEATE: orchestrator dispatches `metaopt-ideation-worker` background agents via `launch_requests` from `metaopt-background-control` handoff. Number of concurrent agents = `proposal_policy.current_target` (no more, no less once pool is full).
- WAIT_FOR_PROPOSALS: no new dispatch. Gate only.
- SELECT_AND_DESIGN_SWEEP: `metaopt-select-design` runs inline (no slot). Orchestrator invokes it as a subagent, reads handoff.
- LOCAL_SANITY: orchestrator executes `run_smoke_test` directive. Reads result file. No subagent dispatch.
- LAUNCH_SWEEP: orchestrator executes `launch_sweep` directive by dispatching `skypilot-wandb-worker`.
- WAIT_FOR_SWEEP: orchestrator executes `poll_sweep` directive by dispatching `skypilot-wandb-worker` on each poll.
- ANALYZE: `metaopt-remote-execution-control` emits `launch_requests` for `metaopt-analysis-worker`. Orchestrator dispatches it as an auxiliary slot.
- ROLL_ITERATION: `metaopt-iteration-close-control` runs inline. No slot.

- [ ] **Step 3: Write `references/dependencies.md`**

Required environment dependencies for the campaign to start:
- SkyPilot installed and configured (`sky check` passes for Vast.ai)
- WandB API key configured (`wandb login` or `WANDB_API_KEY` env var)
- `skypilot-wandb-worker` agent available (verified in HYDRATE_STATE)
- `project.repo` accessible (SSH key or HTTPS credentials for the repo URL)
- `project.smoke_test_command` exists in the repo root

Campaign YAML validation rules (enforced by `metaopt-load-campaign`):
- `compute.max_budget_usd` must be > 0 and <= 100 (hard ceiling; anything larger requires manual override)
- `compute.idle_timeout_minutes` must be between 5 and 60
- `compute.num_sweep_agents` must be between 1 and 16
- `objective.direction` must be `maximize` or `minimize`
- `wandb.entity` and `wandb.project` must be non-empty strings with no whitespace

- [ ] **Step 4: Commit all three**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add references/worker-lanes.md references/dispatch-guide.md references/dependencies.md
git commit -m "docs: rewrite worker-lanes, dispatch-guide, dependencies for v4"
```

---

## Task 12: Rewrite `SKILL.md`

**Files:**
- Rewrite: `SKILL.md`

- [ ] **Step 1: Write the new `SKILL.md`**

The new `SKILL.md` must contain these sections:

**Frontmatter:**
```yaml
---
name: ml-metaoptimization
description: "Use when running a continuous ML training improvement campaign using WandB Sweeps on SkyPilot/Vast.ai. Keywords: metaoptimization, hyperparameter sweep, architecture search, WandB, SkyPilot, Vast.ai, campaign, continuous improvement."
---
```

**Overview (3-4 paragraphs):**
- What it does: runs an iterative ML training campaign. Each iteration: agents propose a WandB sweep search space, the sweep runs on Vast.ai via SkyPilot, results are analyzed, learnings carry forward.
- What it does NOT do: code patches, architecture changes, algorithm optimization. Those belong to `code-optimization` skill.
- Project contract: target project must read hyperparams from `wandb.config` and log metrics via `wandb.log(...)`.
- State machine is persistent across reinvocations via `.ml-metaopt/state.json`.

**Runtime contract:** same model class table as v3 (strong_coder, strong_reasoner, general_worker), updated to remove `strong_coder` since no coders are dispatched.

**Required Files:** updated file tree (no `artifacts/patches`, no worktrees):
```
{project_root}/
  ml_metaopt_campaign.yaml
  AGENTS.md
  .ml-metaopt/
    preflight-readiness.json
    state.json
    handoffs/
    worker-results/
    tasks/
    final_report.md (written on COMPLETE)
```

**Behavioral Guarantees:** (updated set)
- never ask user for campaign-defining inputs
- orchestrator never calls WandB API or SkyPilot CLI directly — only `skypilot-wandb-worker` does
- `LOCAL_SANITY` enforces 60-second hard timeout — not configurable
- `max_budget_usd` default is 10 USD — always enforced
- on crash recovery, reconnect to existing sweep — never launch a new sweep if `current_sweep.sweep_id` exists in state

**Control Agent Dispatch table:** same format as v3, updated for v4 states and agents.

**Quick Flow:** the state machine diagram in dot format (same as in the spec).

**Worker Policy:** two worker classes:
- Background ideation workers: `metaopt-ideation-worker` (propose sweep search spaces)
- Analysis workers: `metaopt-analysis-worker` (read WandB results, update baseline)
- Execution worker: `skypilot-wandb-worker` (directive-dispatched, not a slot)

**Worker Targets table:**

| Lane | Worker | Model Class |
|---|---|---|
| ideation | metaopt-ideation-worker | general_worker |
| analysis | metaopt-analysis-worker | strong_reasoner |
| execution (directive) | skypilot-wandb-worker | general_worker |

**Required References:** same structure, all 7 reference files.

**Orchestrator Actions:** updated list — no patch operations, no slot drain/cancel, no manifest packaging. Include: execute `run_smoke_test`, `launch_sweep`, `poll_sweep`, `remove_agents_hook`, `delete_state_file`, `emit_final_report`, `emit_iteration_report`.

**Common Mistakes table:** (updated)

| Mistake | Fix |
|---|---|
| Calling WandB API or SkyPilot CLI from the orchestrator | Use skypilot-wandb-worker via directive only |
| Launching a new sweep when current_sweep.sweep_id exists in state | Reconnect to the existing sweep in HYDRATE_STATE |
| Letting LOCAL_SANITY run longer than 60 seconds | The 60-second timeout is hardcoded — kill the process |
| Exceeding max_budget_usd without stopping | Budget is checked on every poll_sweep — kill all jobs and BLOCKED_CONFIG |
| Asking user for sweep parameters | Read proposals from current_proposals — if empty, stay in IDEATE |

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add SKILL.md
git commit -m "feat: rewrite SKILL.md for v4 — WandB Sweeps + SkyPilot/Vast.ai"
```

---

## Task 13: Rewrite `metaopt-load-campaign.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-load-campaign.agent.md`

- [ ] **Step 1: Write the agent file**

Frontmatter: `name: metaopt-load-campaign`, model: `claude-sonnet-4`, tools: read, search

**Purpose:** validate `ml_metaopt_campaign.yaml` against the v4 schema from `references/dependencies.md`. Check the preflight artifact. Compute `campaign_identity_hash`. Emit a single-phase handoff.

**Inputs:** `ml_metaopt_campaign.yaml`, `.ml-metaopt/preflight-readiness.json`

**Validation steps (in order):**
1. Check all required top-level keys: `campaign`, `project`, `wandb`, `compute`, `objective`, `proposal_policy`, `stop_conditions`
2. Validate `compute.max_budget_usd` in (0, 100], `compute.idle_timeout_minutes` in [5, 60], `compute.num_sweep_agents` in [1, 16]
3. Validate `objective.direction` is `maximize` or `minimize`
4. Validate `wandb.entity` and `wandb.project` are non-empty
5. Validate `project.smoke_test_command` is non-empty
6. Check for sentinel placeholders (angle brackets, YOUR_*, replace-me)
7. Compute `campaign_identity_hash` per contracts.md Section 4
8. Read `.ml-metaopt/preflight-readiness.json`: if missing → BLOCKED_CONFIG "run metaopt-preflight"; if hash mismatch → BLOCKED_CONFIG "re-run metaopt-preflight"; if status=FAILED → BLOCKED_CONFIG with artifact's next_action; if status=READY → proceed to HYDRATE_STATE

**Output:** write handoff to `.ml-metaopt/handoffs/metaopt-load-campaign-LOAD_CAMPAIGN.json` with `recommended_next_machine_state`, `state_patch` (empty for this agent), `directive: {type: none}`

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-load-campaign.agent.md
git commit -m "feat: rewrite metaopt-load-campaign agent for v4 campaign schema"
```

---

## Task 14: Rewrite `metaopt-hydrate-state.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-hydrate-state.agent.md`

- [ ] **Step 1: Write the agent file**

**Purpose:** Initialize or resume state. Handle crash recovery. Verify `skypilot-wandb-worker` availability.

**Resume logic:**
- If `.ml-metaopt/state.json` exists and `campaign_identity_hash` matches → resume from `machine_state`
- If hash mismatch → BLOCKED_CONFIG "archive or remove stale state"
- If no state file → initialize fresh state from campaign YAML (version=4, current_iteration=0, baseline=null, all lists empty)

**Crash recovery:** if resumed state has `current_sweep.sweep_id` and `machine_state` is in {LAUNCH_SWEEP, WAIT_FOR_SWEEP, ANALYZE}, emit `poll_sweep` directive so the orchestrator immediately reconnects to the existing sweep.

**Worker availability check:** verify `skypilot-wandb-worker` is in the agent registry. If missing → BLOCKED_CONFIG "install skypilot-wandb-worker".

**AGENTS.md hook:** if `status=RUNNING`, ensure the `<!-- ml-metaoptimization:begin -->` block is present. Agent emits a `none` directive if hook already exists; otherwise the orchestrator is instructed to append the standard block.

**State patch:** full state initialization on first run; partial update on resume (only `next_action`).

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-hydrate-state.agent.md
git commit -m "feat: rewrite metaopt-hydrate-state agent for v4 crash recovery and worker check"
```

---

## Task 15: Rewrite `metaopt-background-control.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-background-control.agent.md`

- [ ] **Step 1: Write the agent file**

**Purpose:** manage the ideation proposal pool. Launch `metaopt-ideation-worker` background agents. Gate proposal threshold.

**Plan phase (`plan_ideation`):**
- Count proposals in `current_proposals`
- If `len(current_proposals) < proposal_policy.current_target`, emit `launch_requests` for `metaopt-ideation-worker` agents (one per missing proposal, up to the target)
- Each launch_request: slot_class=background, mode=ideation, model_class=general_worker
- Each agent's task file must include: campaign objective, prior learnings, baseline, all existing proposal rationales (to avoid duplicates)
- Emit `recommended_next_machine_state = null` (stay in IDEATE until gate passes)

**Gate phase (`gate_ideation`):**
- Read all completed ideation worker results from `worker-results/`
- Validate each result has: `proposal_id`, `rationale`, `sweep_config` with valid WandB format
- Detect lane drift: reject any result with `patch_artifacts`, `code_patches`, `code_changes`
- Append valid proposals to `current_proposals` via state_patch
- If `len(current_proposals) >= proposal_policy.current_target` → recommend WAIT_FOR_PROPOSALS
- Else → recommend null (continue ideation)

**WAIT_FOR_PROPOSALS gate:**
- If threshold met → recommend SELECT_AND_DESIGN_SWEEP, set `proposal_cycle.current_pool_frozen = true`
- Else → recommend null

**NO maintenance mode.** v4 background agents do ideation only.

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-background-control.agent.md
git commit -m "feat: rewrite metaopt-background-control for v4 — ideation only, no maintenance"
```

---

## Task 16: Rewrite `metaopt-ideation-worker.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-ideation-worker.agent.md`

- [ ] **Step 1: Write the agent file**

Frontmatter: `name: metaopt-ideation-worker`, `user-invocable: false`, model: `claude-sonnet-4`

**Purpose:** generate one sweep search space proposal.

**Inputs (from task file):**
- Campaign objective: metric name, direction
- Baseline: current best metric value and run details
- Prior learnings: list of key findings from previous iterations
- Existing proposals: list of rationales to avoid duplicates
- Completed iterations: summary of what has been tried

**Output:** write a JSON file to the path specified in `result_file`:

```json
{
  "proposal_id": "prop-<uuid4-short>",
  "rationale": "Why this search space is promising given prior learnings",
  "sweep_config": {
    "method": "bayes",
    "metric": {"name": "<objective.metric>", "goal": "<maximize|minimize>"},
    "parameters": {
      "<param_name>": {
        "<distribution_type>": "..."
      }
    }
  }
}
```

**Rules:**
- `sweep_config.metric.name` must match `objective.metric` exactly
- `sweep_config.metric.goal` must be `maximize` if direction=maximize, `minimize` if direction=minimize
- Parameters must use valid WandB distribution types: `values`, `uniform`, `log_uniform_values`, `int_uniform`, `normal`, `log_normal`, `categorical`, `constant`
- Do NOT produce code patches, file diffs, or any code-change content
- Do NOT repeat a search space already in `existing_proposals` (check rationale overlap)

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-ideation-worker.agent.md
git commit -m "feat: rewrite metaopt-ideation-worker for v4 — outputs WandB sweep config"
```

---

## Task 17: Rewrite `metaopt-select-design.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-select-design.agent.md`

- [ ] **Step 1: Write the agent file**

**Purpose:** select the best proposal from the pool and finalize it as a launch-ready WandB sweep config. Select and design are merged into one agent — no separate design step.

**Plan phase (`plan_select_design`):**
- Read `current_proposals` from state
- Score each proposal against: prior learnings, baseline, iteration history, diversity vs. already-tried configs
- Pick the highest-scoring proposal
- Refine its `sweep_config`: ensure parameter ranges are appropriate for the project scale (not too wide for Bayesian to converge, not too narrow to find improvements)
- Write `state_patch` with `selected_sweep = { proposal_id, sweep_config }` and `proposal_cycle.current_pool_frozen = true`
- Recommend `LOCAL_SANITY`

**Finalize phase (`finalize_select_design`):** not needed — single-phase for this agent. Skip gate.

**Output:** handoff with `state_patch.selected_sweep` and `directive: {type: none}` (no execution directive at this stage — LOCAL_SANITY is next).

**Rules:**
- Never modify the `current_proposals` list — only write to `selected_sweep`
- The final sweep_config must have at least 2 parameters (trivial 1-parameter sweeps waste budget)
- The `method` must be `bayes` unless the parameter space is entirely categorical (then `grid` is acceptable for small spaces, `random` for large)

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-select-design.agent.md
git commit -m "feat: rewrite metaopt-select-design agent for v4 — merged select+design, outputs sweep config"
```

---

## Task 18: Rewrite `metaopt-remote-execution-control.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-remote-execution-control.agent.md`

- [ ] **Step 1: Write the agent file**

**Purpose:** govern LOCAL_SANITY, LAUNCH_SWEEP, WAIT_FOR_SWEEP, and ANALYZE states.

**LOCAL_SANITY phase:**
- Emit `run_smoke_test` directive: `{ "command": <project.smoke_test_command>, "result_file": ".ml-metaopt/worker-results/smoke-<iter>.json" }`
- After orchestrator runs and writes result: read result file. If `exit_code != 0` or `timed_out == true` → recommend FAILED with descriptive `next_action`. Else → recommend LAUNCH_SWEEP.

**LAUNCH_SWEEP phase:**
- Emit `launch_sweep` directive: `{ "sweep_config": <state.selected_sweep.sweep_config>, "sky_task_spec": { "repo": <project.repo>, "accelerator": <compute.accelerator>, "num_agents": <compute.num_sweep_agents>, "idle_timeout_minutes": <compute.idle_timeout_minutes> }, "result_file": ".ml-metaopt/worker-results/launch-sweep-<iter>.json" }`
- After orchestrator executes and writes result: read result file. Apply `state_patch` with `current_sweep = { sweep_id, sweep_url, sky_job_ids, launched_at, cumulative_spend_usd: 0 }`. Recommend WAIT_FOR_SWEEP.

**WAIT_FOR_SWEEP phase (poll loop):**
- Emit `poll_sweep` directive: `{ "sweep_id": <state.current_sweep.sweep_id>, "sky_job_ids": <state.current_sweep.sky_job_ids>, "idle_timeout_minutes": <compute.idle_timeout_minutes>, "max_budget_usd": <compute.max_budget_usd>, "result_file": ".ml-metaopt/worker-results/poll-sweep-<iter>-<ts>.json" }`
- After result: update `state.current_sweep.cumulative_spend_usd`
- If `sweep_status == "running"` → recommend null (poll again next session)
- If `sweep_status == "completed"` → recommend ANALYZE
- If `sweep_status == "failed"` (all agents crashed, no successful runs) → recommend FAILED
- If `sweep_status == "budget_exceeded"` → recommend BLOCKED_CONFIG "budget cap of $<max> reached"

**ANALYZE phase:**
- Emit `launch_requests` for `metaopt-analysis-worker` (slot_class=auxiliary, mode=analysis, model_class=strong_reasoner)
- Agent's task file must include: best WandB run ID and URL from completed sweep, current baseline, prior learnings
- After worker completes: read result. If `improved == true`: apply baseline update via state_patch. Append to `completed_iterations`. Update `no_improve_iterations`. Recommend ROLL_ITERATION.

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-remote-execution-control.agent.md
git commit -m "feat: rewrite metaopt-remote-execution-control for v4 — LOCAL_SANITY through ANALYZE"
```

---

## Task 19: Rewrite `metaopt-iteration-close-control.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-iteration-close-control.agent.md`

- [ ] **Step 1: Write the agent file**

**Purpose:** roll the iteration, check stop conditions, carry proposals forward, emit iteration report.

**Roll phase:**
1. Filter `next_proposals`: remove any that duplicate `completed_iterations` sweep configs or contradict `key_learnings`
2. Move filtered `next_proposals` → `current_proposals`. Clear `next_proposals`.
3. Increment `current_iteration` (only if campaign will continue)
4. Reset `current_sweep` to `null`, `selected_sweep` to `null`
5. Create new `proposal_cycle`: `{ "cycle_id": "iter-<N>-cycle-1", "current_pool_frozen": false }`

**Stop condition check (direction-aware, from contracts.md Section 5):**
- If `baseline.value` meets `objective.target_metric` → COMPLETE
- If `current_iteration >= stop_conditions.max_iterations` → COMPLETE
- If `no_improve_iterations >= stop_conditions.max_no_improve_iterations` → COMPLETE
- If `current_sweep.cumulative_spend_usd >= compute.max_budget_usd` → BLOCKED_CONFIG

**Emit `emit_iteration_report` directive:** `{ "iteration": N, "best_metric": <value>, "spend_usd": <float>, "sweep_url": <url>, "proposal_rationale": <string> }`

If continuing: recommend IDEATE. If stopping: recommend COMPLETE (also emit `remove_agents_hook`, `delete_state_file`, `emit_final_report` directives — three separate directive calls across reinvocations, or as a sequence in the handoff).

**Note on absorbed rollover:** in v3 a separate `metaopt-rollover-worker` filtered proposals. In v4 this agent does the filtering inline — no separate dispatch.

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-iteration-close-control.agent.md
git commit -m "feat: rewrite metaopt-iteration-close-control for v4 — absorbs rollover, direction-aware stop"
```

---

## Task 20: Rewrite `metaopt-analysis-worker.agent.md`

**Files:**
- Rewrite: `.github/agents/metaopt-analysis-worker.agent.md`

- [ ] **Step 1: Write the agent file**

Frontmatter: `name: metaopt-analysis-worker`, `user-invocable: false`, model: `claude-opus-4.6`

**Purpose:** analyze the best run from a completed WandB sweep and update the baseline.

**Inputs (from task file):**
- WandB best run ID and URL
- WandB sweep URL
- Current baseline (metric, value)
- Objective: metric name, direction, improvement_threshold
- Prior key_learnings

**Steps:**
1. Query WandB API for the best run's full config (hyperparams used) and final metric value
2. Compare against baseline using direction-aware comparison from contracts.md Section 5
3. Extract 1-3 key learnings: what worked, what didn't, what parameter combinations are promising

**Output JSON written to `result_file`:**
```json
{
  "improved": true,
  "new_baseline": {
    "metric": "val/accuracy",
    "value": 0.957,
    "wandb_run_id": "run-abc",
    "wandb_run_url": "https://...",
    "established_at": "<ISO 8601>"
  },
  "learnings": [
    "num_layers=3 with use_residual=true outperforms num_layers=4 without residual"
  ],
  "best_run_id": "run-abc",
  "best_run_config": { "lr": 0.003, "num_layers": 3, "use_residual": true }
}
```

If `improved == false`, `new_baseline` is `null`.

**Rules:**
- Never emit sweep configs or code change suggestions
- Learnings must be specific and falsifiable (not "model performed well")

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/metaopt-analysis-worker.agent.md
git commit -m "feat: rewrite metaopt-analysis-worker for v4 — WandB run analysis and baseline update"
```

---

## Task 21: Create `skypilot-wandb-worker.agent.md`

**Files:**
- Create: `.github/agents/skypilot-wandb-worker.agent.md`

- [ ] **Step 1: Write the agent file**

Frontmatter: `name: skypilot-wandb-worker`, `user-invocable: false`, model: `claude-sonnet-4`, tools: execute, read

**Purpose:** leaf worker for all remote execution. Dispatched by the orchestrator for `launch_sweep` and `poll_sweep` directives. Executes exactly one operation per invocation.

**Operation: `launch_sweep`**

Inputs from task file: `sweep_config`, `sky_task_spec` (repo, accelerator, num_agents, idle_timeout_minutes), `result_file`, WandB entity/project.

Steps:
1. Create WandB sweep: `wandb sweep --project <project> --entity <entity> <sweep_config_yaml_path>` → captures `sweep_id`
2. For each agent (1 to num_agents): `sky launch --idle-minutes-to-autostop <idle_timeout_minutes> --accelerator <accelerator> -- "wandb agent <entity>/<project>/<sweep_id>"` → captures `job_id`
3. If any `sky launch` fails after sweep is created: `wandb sweep cancel <sweep_id>` then write error to result_file and exit non-zero
4. Write success JSON to `result_file`: `{ "operation": "launch_sweep", "sweep_id", "sweep_url", "sky_job_ids", "launched_at" }`

**Operation: `poll_sweep`**

Inputs from task file: `sweep_id`, `sky_job_ids`, `idle_timeout_minutes`, `max_budget_usd`, `cumulative_spend_usd_so_far`, `result_file`, WandB entity/project.

Steps:
1. Query WandB API: `wandb.Api().sweep("<entity>/<project>/<sweep_id>")` — get `state`, `best_run`, runs list
2. For each active run: check `run.summary.get("_timestamp")`. If `now - last_log > idle_timeout_minutes * 60`: `sky down <job_id>` + `run.finish(exit_code=1)` + append to `killed_runs`
3. Query cost: `sky cost` or SkyPilot cost API → get cumulative spend. If `cumulative + prior >= max_budget_usd`: kill all remaining jobs, set `sweep_status = "budget_exceeded"`
4. Determine sweep_status: wandb sweep state `"finished"` → `"completed"`, `"crashed"` with no successful runs → `"failed"`, else → `"running"`
5. Write result JSON to `result_file`: `{ "operation": "poll_sweep", "sweep_status", "best_metric_value", "best_run_id", "killed_runs", "cumulative_spend_usd" }`

**Operation: `run_smoke_test`**

Inputs: `command`, `result_file`.

Steps:
1. Run `command` with a 60-second hard timeout: `timeout 60 bash -c "<command>"` (or Python `subprocess.run(..., timeout=60)`)
2. Capture stdout, stderr (last 200 lines each), exit_code. If timeout exceeded: `timed_out=True`, `exit_code=124`
3. Write result: `{ "operation": "run_smoke_test", "exit_code", "timed_out", "stdout_tail", "stderr_tail" }`

**Rules:**
- Never mutate `.ml-metaopt/state.json`
- Never re-enqueue a failed sweep
- Never run more than the specified number of agents
- If the WandB API is unreachable, write an error result and exit non-zero — do not retry

- [ ] **Step 2: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add .github/agents/skypilot-wandb-worker.agent.md
git commit -m "feat: add skypilot-wandb-worker agent — launch_sweep, poll_sweep, run_smoke_test"
```

---

## Task 22: Update handoff scripts

**Files:**
- Modify: `scripts/remote_execution_control_handoff.py`
- Review and update others as needed: `scripts/background_control_handoff.py`, `scripts/select_and_design_handoff.py`, `scripts/iteration_close_control_handoff.py`, `scripts/hydrate_state_handoff.py`, `scripts/load_campaign_handoff.py`

- [ ] **Step 1: Read each handoff script**

Read each file in `scripts/` to identify v3-specific fields:

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
head -60 scripts/remote_execution_control_handoff.py
head -60 scripts/background_control_handoff.py
```

- [ ] **Step 2: Update `remote_execution_control_handoff.py`**

Remove all references to: `queue_op`, `enqueue`, `batch_id`, `write_manifest`, `run_sanity`, `drain_slots`, `cancel_slots`.

Add helper functions for v4 directives:

```python
def make_launch_sweep_directive(sweep_config: dict, sky_task_spec: dict, iter_num: int) -> dict:
    return {
        "type": "launch_sweep",
        "payload": {
            "sweep_config": sweep_config,
            "sky_task_spec": sky_task_spec,
            "result_file": f".ml-metaopt/worker-results/launch-sweep-iter-{iter_num}.json",
        },
    }


def make_poll_sweep_directive(sweep_id: str, sky_job_ids: list, iter_num: int, ts: str) -> dict:
    return {
        "type": "poll_sweep",
        "payload": {
            "sweep_id": sweep_id,
            "sky_job_ids": sky_job_ids,
            "result_file": f".ml-metaopt/worker-results/poll-sweep-iter-{iter_num}-{ts}.json",
        },
    }


def make_run_smoke_test_directive(command: str, iter_num: int) -> dict:
    return {
        "type": "run_smoke_test",
        "payload": {
            "command": command,
            "result_file": f".ml-metaopt/worker-results/smoke-test-iter-{iter_num}.json",
        },
    }
```

- [ ] **Step 3: Update remaining handoff scripts for v4 state fields**

In `background_control_handoff.py`: remove references to `ideation_rounds_by_slot`, `active_slots`, `maintenance_summary`. Update state patch keys to match v4 ownership.

In `select_and_design_handoff.py`: remove `selected_experiment.design` references. Update to use `selected_sweep`.

In `iteration_close_control_handoff.py`: remove `QUIESCE_SLOTS`, `drain_slots`, slot accounting. Update stop condition checks.

In `hydrate_state_handoff.py`: remove `runtime_capabilities.available_skills` list. Update to v4 state init.

- [ ] **Step 4: Run the full test suite**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: all non-campaign-example tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add scripts/
git commit -m "refactor: update handoff scripts for v4 state fields and directive types"
```

---

## Task 23: Rewrite `ml_metaopt_campaign.example.yaml`

**Files:**
- Rewrite: `ml_metaopt_campaign.example.yaml`

- [ ] **Step 1: Write the v4 campaign example**

```yaml
# ml_metaopt_campaign.example.yaml
# v4 — WandB Sweeps + SkyPilot + Vast.ai

campaign:
  name: gnn-mnist-optimization
  description: "Optimize GNN hyperparameters for MNIST graph classification accuracy"

project:
  repo: git@github.com:my-org/dg_image.git
  # Must not crash within 60 seconds. Reads wandb.config, logs to wandb.
  smoke_test_command: "python train.py --smoke --max-steps 10"

wandb:
  entity: my-wandb-entity
  project: gnn-mnist-metaopt

compute:
  provider: vast_ai          # SkyPilot cloud provider
  accelerator: A100:1        # Instance type requested from Vast.ai
  num_sweep_agents: 4        # Parallel WandB agents per sweep
  idle_timeout_minutes: 15   # Kill agent if no WandB logs for this long
  max_budget_usd: 10         # Hard spend cap — enforced on every poll

objective:
  metric: val/accuracy
  direction: maximize        # maximize | minimize
  improvement_threshold: 0.005  # Minimum gain to count as improvement

proposal_policy:
  current_target: 5          # Proposals needed in pool before selection

stop_conditions:
  max_iterations: 20
  target_metric: 0.990       # Stop when baseline reaches this value
  max_no_improve_iterations: 5
```

- [ ] **Step 2: Run the full test suite including validation**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add ml_metaopt_campaign.example.yaml
git commit -m "feat: rewrite campaign example YAML for v4 — WandB/SkyPilot/Vast.ai"
```

---

## Task 24: Final integration check

**Files:** no changes — verification only.

- [ ] **Step 1: Run the full test suite**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
python -m pytest tests/ -v
```

Expected: all tests pass with no warnings about missing files or deprecated fields.

- [ ] **Step 2: Verify all obsolete files are gone**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
ls .github/agents/
```

Expected: `metaopt-load-campaign.agent.md`, `metaopt-hydrate-state.agent.md`, `metaopt-background-control.agent.md`, `metaopt-ideation-worker.agent.md`, `metaopt-select-design.agent.md`, `metaopt-remote-execution-control.agent.md`, `metaopt-iteration-close-control.agent.md`, `metaopt-analysis-worker.agent.md`, `skypilot-wandb-worker.agent.md`.

Absent: `hetzner-delegation-worker.agent.md`, `metaopt-design-worker.agent.md`, `metaopt-materialization-worker.agent.md`, `metaopt-diagnosis-worker.agent.md`, `metaopt-rollover-worker.agent.md`, `metaopt-local-execution-control.agent.md`.

- [ ] **Step 3: Verify SKILL.md references only v4 states and workers**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
grep -n "hetzner\|QUIESCE_SLOTS\|MATERIALIZE_CHANGESET\|local_changeset\|active_slots\|batch_id\|metaopt-rollover\|metaopt-diagnosis\|metaopt-materialization" SKILL.md
```

Expected: no matches.

- [ ] **Step 4: Final commit**

```bash
cd /home/jakub/.claude/skills/ml-metaoptimization
git add -u
git commit -m "chore: final integration check — v4 redesign complete"
```

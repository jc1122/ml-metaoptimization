# ML Metaoptimization V3 Contract Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the `ml-metaoptimization` contract repo from v2 to v3 so resume bookkeeping, patch/data artifacts, retry ownership, reinvocation semantics, and dependency requirements are explicit and statically validated.

**Architecture:** Treat the repository as a contract package. First make the validation suite expect the new v3 surfaces in narrow slices, then update the example campaign, fixtures, and markdown contracts in the same slices so each targeted test returns to green before expanding coverage. Keep the control-plane state machine intact; harden the public contract around it.

**Tech Stack:** Markdown, YAML, JSON fixtures, Python `unittest`, PyYAML, git

---

## File Structure

- Create: `docs/superpowers/plans/2026-04-05-ml-metaoptimization-v3-contract-cleanup.md`
- Create: `requirements.txt`
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `ml_metaopt_campaign.example.yaml`
- Modify: `references/contracts.md`
- Modify: `references/state-machine.md`
- Modify: `references/worker-lanes.md`
- Modify: `references/backend-contract.md`
- Modify: `references/dependencies.md`
- Modify: `tests/test_metaopt_validation.py`
- Modify: `tests/fixtures/backend/results-valid.json`
- Modify: `tests/fixtures/state/running.json`
- Modify: `tests/fixtures/state/complete.json`
- Modify: `tests/fixtures/state/invalid-status-pair.json`
- Create: `tests/fixtures/state/invalid-missing-proposal-cycle.json`
- Create: `tests/fixtures/state/invalid-missing-slot-model-resolution.json`
- Create: `tests/fixtures/state/invalid-missing-local-changeset-metadata.json`

### Responsibility Map

- `tests/test_metaopt_validation.py` stays the single static validator for the repo's public contract.
- `ml_metaopt_campaign.example.yaml` is the canonical v3 campaign example.
- `tests/fixtures/state/*.json` pin valid and invalid persisted v3 runtime state.
- `references/contracts.md` owns field-level v3 state, slot, changeset, and manifest definitions.
- `references/state-machine.md` owns transition semantics and resume/quiesce rules.
- `references/worker-lanes.md` owns worker output rules, patch artifacts, and model resolution expectations.
- `references/backend-contract.md` owns queue/backend IO and retry-policy ownership.
- `references/dependencies.md` and `README.md` own operator-facing prerequisites.
- `SKILL.md` owns the top-level runtime narrative and required repository layout.

### Task 1: Promote the example campaign to the v3 retry-policy shape

**Files:**
- Modify: `tests/test_metaopt_validation.py`
- Modify: `ml_metaopt_campaign.example.yaml`
- Test: `python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_example_campaign_is_v3_and_free_of_sentinel_values -v`

- [ ] **Step 1: Write the failing campaign-version test**

```python
def test_example_campaign_is_v3_and_free_of_sentinel_values(self) -> None:
    campaign = _read_yaml("ml_metaopt_campaign.example.yaml")

    self.assertEqual(campaign["version"], 3)
    self.assertEqual(campaign["remote_queue"]["backend"], "ray-hetzner")

    retry_policy = campaign["remote_queue"]["retry_policy"]
    self.assertEqual(retry_policy["max_attempts"], 2)

    for command_name in ("enqueue_command", "status_command", "results_command"):
        command = campaign["remote_queue"][command_name]
        self.assertNotRegex(command, r"[<>]")
        self.assertNotIn("YOUR_", command)

    for dataset in campaign["datasets"]:
        self.assertRegex(dataset["fingerprint"], r"^sha256:[0-9a-f]{64}$")

    self.assertNotIn("max_batch_retries", campaign["execution"])
    self.assertNotIn("/root/project/", campaign["execution"]["entrypoint"])
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_example_campaign_is_v3_and_free_of_sentinel_values -v
```

Expected: FAIL because `ml_metaopt_campaign.example.yaml` still uses `version: 2` and does not define `remote_queue.retry_policy`.

- [ ] **Step 3: Update the example campaign to the v3 shape**

```yaml
version: 3
campaign_id: market-forecast-v3
goal: Improve out-of-sample forecast quality without temporal leakage.

remote_queue:
  backend: ray-hetzner
  retry_policy:
    max_attempts: 2
  enqueue_command: python3 /opt/ray-hetzner/metaopt/enqueue_batch.py
  status_command: python3 /opt/ray-hetzner/metaopt/get_batch_status.py
  results_command: python3 /opt/ray-hetzner/metaopt/fetch_batch_results.py

execution:
  runner_type: ray_queue_runner
  entrypoint: python3 /srv/metaopt/project/scripts/ray_runner.py
  target_cluster_utilization: 0.95
  trial_budget:
    kind: fixed_trials
    value: 128
  search_strategy:
    kind: optuna_tpe
    seed: 1337
```

Apply that shape to `ml_metaopt_campaign.example.yaml`, keeping the existing objective, datasets, stop conditions, sanity, and artifact paths intact while deleting `execution.max_batch_retries`.

- [ ] **Step 4: Run the targeted test to verify it passes**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_example_campaign_is_v3_and_free_of_sentinel_values -v
```

Expected: PASS

- [ ] **Step 5: Commit the campaign-shape change**

```bash
git add tests/test_metaopt_validation.py ml_metaopt_campaign.example.yaml
git commit -m "test: expect v3 campaign contract"
```

### Task 2: Require resumable v3 state bookkeeping and richer changeset metadata

**Files:**
- Modify: `tests/test_metaopt_validation.py`
- Modify: `tests/fixtures/state/running.json`
- Modify: `tests/fixtures/state/complete.json`
- Modify: `tests/fixtures/state/invalid-status-pair.json`
- Create: `tests/fixtures/state/invalid-missing-proposal-cycle.json`
- Create: `tests/fixtures/state/invalid-missing-slot-model-resolution.json`
- Create: `tests/fixtures/state/invalid-missing-local-changeset-metadata.json`
- Test: `python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_v3_state_fixtures_require_resume_and_changeset_metadata -v`

- [ ] **Step 1: Write the failing v3 state-fixture test**

```python
def test_v3_state_fixtures_require_resume_and_changeset_metadata(self) -> None:
    running = _read_json("tests/fixtures/state/running.json")
    complete = _read_json("tests/fixtures/state/complete.json")

    self.assertEqual(running["version"], 3)
    self.assertEqual(complete["version"], 3)
    self.assertIn("proposal_cycle", running)
    self.assertIn("integration_worktree", running["local_changeset"])
    self.assertIn("data_manifest_uri", running["local_changeset"])
    self.assertIn("model_class", running["active_slots"][0])
    self.assertIn("requested_model", running["active_slots"][0])
    self.assertIn("resolved_model", running["active_slots"][0])
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_v3_state_fixtures_require_resume_and_changeset_metadata -v
```

Expected: FAIL because the current fixtures are still v2-shaped and do not contain `proposal_cycle` or the richer slot/changeset fields.

- [ ] **Step 3: Upgrade the validator helper and the state fixtures together**

```python
VALID_MODEL_CLASSES = {"strong_coder", "strong_reasoner", "general_worker"}

def _validate_state_payload(payload: dict) -> None:
    required_keys = {
        "version",
        "campaign_id",
        "campaign_identity_hash",
        "runtime_config_hash",
        "status",
        "machine_state",
        "current_iteration",
        "next_action",
        "objective_snapshot",
        "proposal_cycle",
        "active_slots",
        "current_proposals",
        "next_proposals",
        "selected_experiment",
        "local_changeset",
        "remote_batches",
        "baseline",
        "completed_experiments",
        "key_learnings",
        "no_improve_iterations",
    }
    missing = required_keys - payload.keys()
    assert not missing, f"missing required state keys: {sorted(missing)}"
    assert payload["version"] == 3, "state fixture must use v3"

    proposal_cycle = payload["proposal_cycle"]
    assert isinstance(proposal_cycle.get("cycle_id"), str) and proposal_cycle["cycle_id"]
    assert isinstance(proposal_cycle.get("current_pool_frozen"), bool)
    assert isinstance(proposal_cycle.get("ideation_rounds_by_slot"), dict)
    assert "shortfall_reason" in proposal_cycle

    for slot in payload["active_slots"]:
        assert slot["slot_class"] in VALID_SLOT_CLASSES, "invalid slot_class"
        assert slot["mode"] in VALID_SLOT_MODES, "invalid slot mode"
        assert slot["model_class"] in VALID_MODEL_CLASSES, "invalid model_class"
        assert isinstance(slot.get("requested_model"), str) and slot["requested_model"]
        assert isinstance(slot.get("resolved_model"), str) and slot["resolved_model"]
        assert isinstance(slot["task_summary"], str) and slot["task_summary"]

    local_changeset = payload["local_changeset"]
    assert isinstance(local_changeset.get("integration_worktree"), str) and local_changeset["integration_worktree"]
    assert isinstance(local_changeset.get("patch_artifacts"), list), "patch_artifacts list is required"
    assert isinstance(local_changeset.get("apply_results"), list), "apply_results list is required"
    assert isinstance(local_changeset.get("verification_notes"), list), "verification_notes list is required"
    assert isinstance(local_changeset.get("code_artifact_uri"), str) and local_changeset["code_artifact_uri"]
    assert isinstance(local_changeset.get("data_manifest_uri"), str) and local_changeset["data_manifest_uri"]
```

```json
{
  "version": 3,
  "proposal_cycle": {
    "cycle_id": "iter-3-cycle-1",
    "current_pool_frozen": true,
    "ideation_rounds_by_slot": {
      "bg-1": 2,
      "bg-2": 2
    },
    "shortfall_reason": ""
  },
  "active_slots": [
    {
      "slot_id": "bg-1",
      "slot_class": "background",
      "mode": "maintenance",
      "model_class": "general_worker",
      "requested_model": "GPT-5.4",
      "resolved_model": "GPT-5.4",
      "status": "running",
      "attempt": 1,
      "task_summary": "Read-only maintenance audit while the remote batch runs"
    }
  ],
  "local_changeset": {
    "integration_worktree": ".ml-metaopt/worktrees/iter-3-materialization",
    "patch_artifacts": [],
    "apply_results": [],
    "verification_notes": [
      "local sanity has not rerun yet"
    ],
    "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-20260405-0001.tar.gz",
    "data_manifest_uri": ".ml-metaopt/artifacts/data/batch-20260405-0001.json"
  }
}
```

Use the first JSON block as the shape to fold into `running.json` and `complete.json` with appropriate values. Also upgrade `invalid-status-pair.json` to the same v3 field set while preserving the intentional terminal/coarse-status mismatch. Then add these new negative fixtures:

```json
{
  "version": 3,
  "campaign_id": "market-forecast-v3",
  "campaign_identity_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "runtime_config_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "status": "RUNNING",
  "machine_state": "WAIT_FOR_REMOTE_BATCH",
  "current_iteration": 3,
  "next_action": "poll remote batch status",
  "objective_snapshot": {
    "metric": "rmse",
    "direction": "minimize",
    "aggregation": {
      "method": "weighted_mean",
      "weights": {
        "ds_main": 0.7,
        "ds_holdout": 0.3
      }
    },
    "improvement_threshold": 0.0005
  },
  "active_slots": [],
  "current_proposals": [],
  "next_proposals": [],
  "selected_experiment": {},
  "local_changeset": {
    "integration_worktree": ".ml-metaopt/worktrees/iter-3-materialization",
    "patch_artifacts": [],
    "apply_results": [],
    "verification_notes": [],
    "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-20260405-0001.tar.gz",
    "data_manifest_uri": ".ml-metaopt/artifacts/data/batch-20260405-0001.json"
  },
  "remote_batches": [],
  "baseline": {
    "aggregate": 0.1284,
    "by_dataset": {
      "ds_main": 0.1269,
      "ds_holdout": 0.1320
    }
  },
  "completed_experiments": [],
  "key_learnings": [],
  "no_improve_iterations": 1
}
```

Save that block as `tests/fixtures/state/invalid-missing-proposal-cycle.json`.

```json
{
  "version": 3,
  "campaign_id": "market-forecast-v3",
  "campaign_identity_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "runtime_config_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "status": "RUNNING",
  "machine_state": "WAIT_FOR_REMOTE_BATCH",
  "current_iteration": 3,
  "next_action": "poll remote batch status",
  "objective_snapshot": {
    "metric": "rmse",
    "direction": "minimize",
    "aggregation": {
      "method": "weighted_mean",
      "weights": {
        "ds_main": 0.7,
        "ds_holdout": 0.3
      }
    },
    "improvement_threshold": 0.0005
  },
  "proposal_cycle": {
    "cycle_id": "iter-3-cycle-1",
    "current_pool_frozen": true,
    "ideation_rounds_by_slot": {
      "bg-1": 2
    },
    "shortfall_reason": ""
  },
  "active_slots": [
    {
      "slot_id": "bg-1",
      "slot_class": "background",
      "mode": "maintenance",
      "status": "running",
      "attempt": 1,
      "task_summary": "Read-only maintenance audit while the remote batch runs"
    }
  ],
  "current_proposals": [],
  "next_proposals": [],
  "selected_experiment": {},
  "local_changeset": {
    "integration_worktree": ".ml-metaopt/worktrees/iter-3-materialization",
    "patch_artifacts": [],
    "apply_results": [],
    "verification_notes": [],
    "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-20260405-0001.tar.gz",
    "data_manifest_uri": ".ml-metaopt/artifacts/data/batch-20260405-0001.json"
  },
  "remote_batches": [],
  "baseline": {
    "aggregate": 0.1284,
    "by_dataset": {
      "ds_main": 0.1269,
      "ds_holdout": 0.1320
    }
  },
  "completed_experiments": [],
  "key_learnings": [],
  "no_improve_iterations": 1
}
```

Save that block as `tests/fixtures/state/invalid-missing-slot-model-resolution.json`.

```json
{
  "version": 3,
  "campaign_id": "market-forecast-v3",
  "campaign_identity_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "runtime_config_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "status": "RUNNING",
  "machine_state": "WAIT_FOR_REMOTE_BATCH",
  "current_iteration": 3,
  "next_action": "poll remote batch status",
  "objective_snapshot": {
    "metric": "rmse",
    "direction": "minimize",
    "aggregation": {
      "method": "weighted_mean",
      "weights": {
        "ds_main": 0.7,
        "ds_holdout": 0.3
      }
    },
    "improvement_threshold": 0.0005
  },
  "proposal_cycle": {
    "cycle_id": "iter-3-cycle-1",
    "current_pool_frozen": true,
    "ideation_rounds_by_slot": {
      "bg-1": 2
    },
    "shortfall_reason": ""
  },
  "active_slots": [
    {
      "slot_id": "bg-1",
      "slot_class": "background",
      "mode": "maintenance",
      "model_class": "general_worker",
      "requested_model": "GPT-5.4",
      "resolved_model": "GPT-5.4",
      "status": "running",
      "attempt": 1,
      "task_summary": "Read-only maintenance audit while the remote batch runs"
    }
  ],
  "current_proposals": [],
  "next_proposals": [],
  "selected_experiment": {},
  "local_changeset": {
    "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-20260405-0001.tar.gz"
  },
  "remote_batches": [],
  "baseline": {
    "aggregate": 0.1284,
    "by_dataset": {
      "ds_main": 0.1269,
      "ds_holdout": 0.1320
    }
  },
  "completed_experiments": [],
  "key_learnings": [],
  "no_improve_iterations": 1
}
```

Save that block as `tests/fixtures/state/invalid-missing-local-changeset-metadata.json`.

- [ ] **Step 4: Run the targeted test to verify it passes**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_v3_state_fixtures_require_resume_and_changeset_metadata -v
```

Expected: PASS

- [ ] **Step 5: Commit the state-fixture upgrade**

```bash
git add tests/test_metaopt_validation.py tests/fixtures/state
git commit -m "test: require v3 state resume metadata"
```

### Task 3: Define the v3 state, manifest, and retry contract in the reference docs

**Files:**
- Modify: `tests/test_metaopt_validation.py`
- Modify: `references/contracts.md`
- Modify: `references/backend-contract.md`
- Modify: `tests/fixtures/backend/results-valid.json`
- Test: `python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_contract_docs_define_v3_state_manifest_and_retry_policy -v`

- [ ] **Step 1: Write the failing contracts-doc test**

```python
def test_contract_docs_define_v3_state_manifest_and_retry_policy(self) -> None:
    contracts = _read_text("references/contracts.md")
    backend = _read_text("references/backend-contract.md")

    _require_pattern(
        self,
        contracts,
        r"## State File.*`proposal_cycle`.*`current_pool_frozen`.*`ideation_rounds_by_slot`",
    )
    _require_pattern(
        self,
        contracts,
        r"## Slot Contract.*`model_class`.*`requested_model`.*`resolved_model`",
    )
    _require_pattern(
        self,
        contracts,
        r"## Local Changeset Contract.*`integration_worktree`.*`patch_artifacts`.*`data_manifest_uri`",
    )
    _require_pattern(
        self,
        contracts,
        r"## Batch Manifest Contract.*`artifacts\\.code_artifact\\.uri`.*`artifacts\\.data_manifest\\.uri`",
    )
    _require_pattern(
        self,
        backend,
        r"## Retry Policy Contract.*backend.*must honor the declared retry policy",
    )
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_contract_docs_define_v3_state_manifest_and_retry_policy -v
```

Expected: FAIL because the current docs do not define `proposal_cycle`, a local-changeset contract, or an explicit retry-policy section.

- [ ] **Step 3: Rewrite the contract sections and update the backend result fixture**

```markdown
## State File

Required top-level keys:
- `version`
- `campaign_id`
- `campaign_identity_hash`
- `runtime_config_hash`
- `status`
- `machine_state`
- `current_iteration`
- `next_action`
- `objective_snapshot`
- `proposal_cycle`
- `active_slots`
- `current_proposals`
- `next_proposals`
- `selected_experiment`
- `local_changeset`
- `remote_batches`
- `baseline`
- `completed_experiments`
- `key_learnings`
- `no_improve_iterations`
```

```markdown
## Slot Contract

Each active slot must record:
- `slot_id`
- `slot_class`
- `mode`
- `model_class`
- `requested_model`
- `resolved_model`
- `status`
- `attempt`
- `task_summary`
```

```markdown
## Local Changeset Contract

Each `local_changeset` must record:
- `integration_worktree`
- `patch_artifacts`
- `apply_results`
- `verification_notes`
- `code_artifact_uri`
- `data_manifest_uri`
```

```markdown
## Batch Manifest Contract

Required manifest fields:
- `version`
- `campaign_id`
- `iteration`
- `batch_id`
- `experiment`
- `artifacts.code_artifact.uri`
- `artifacts.data_manifest.uri`
- `execution.entrypoint`
```

```markdown
## Retry Policy Contract

The orchestrator declares retry policy in the campaign spec and batch manifest.
The backend must honor the declared retry policy.
If the selected backend cannot honor it, the run must fail before enqueue.
```

```json
{
  "batch_id": "batch-20260405-0001",
  "status": "completed",
  "best_aggregate_result": {
    "metric": "rmse",
    "value": 0.1213
  },
  "per_dataset": {
    "ds_main": 0.1208,
    "ds_holdout": 0.1225
  },
  "artifact_locations": {
    "code": "s3://metaopt/artifacts/code/batch-20260405-0001.tar.gz",
    "data_manifest": "s3://metaopt/artifacts/data/batch-20260405-0001.json",
    "metrics": "s3://metaopt/results/batch-20260405-0001/metrics.json"
  },
  "logs_location": "s3://metaopt/results/batch-20260405-0001/logs.txt"
}
```

Use those blocks to rewrite `references/contracts.md`, add the retry section to `references/backend-contract.md`, and update `tests/fixtures/backend/results-valid.json` so the artifact surface includes `data_manifest`.

- [ ] **Step 4: Run the targeted test to verify it passes**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_contract_docs_define_v3_state_manifest_and_retry_policy -v
```

Expected: PASS

- [ ] **Step 5: Commit the v3 reference-contract update**

```bash
git add tests/test_metaopt_validation.py references/contracts.md references/backend-contract.md tests/fixtures/backend/results-valid.json
git commit -m "docs: define v3 metaopt state and manifest contract"
```

### Task 4: Align the state machine, worker lanes, and top-level skill narrative

**Files:**
- Modify: `tests/test_metaopt_validation.py`
- Modify: `references/state-machine.md`
- Modify: `references/worker-lanes.md`
- Modify: `SKILL.md`
- Test: `python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_runtime_docs_define_reinvocation_and_patch_artifacts -v`

- [ ] **Step 1: Write the failing runtime-docs test**

```python
def test_runtime_docs_define_reinvocation_and_patch_artifacts(self) -> None:
    skill = _read_text("SKILL.md")
    machine = _read_text("references/state-machine.md")
    lanes = _read_text("references/worker-lanes.md")

    _require_pattern(self, skill, r"continuous across reinvocations")
    _require_pattern(self, skill, r"artifacts/.*code/.*data/.*manifests/.*patches/")
    _require_pattern(
        self,
        machine,
        r"`proposal_cycle`.*`ideation_rounds_by_slot`.*floor rule",
    )
    _require_pattern(
        self,
        machine,
        r"record cancellation reasons in state.*`apply_results`",
    )
    _require_pattern(
        self,
        lanes,
        r"unified diff patch artifact.*`producer_slot_id`.*`patch_path`",
    )
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_runtime_docs_define_reinvocation_and_patch_artifacts -v
```

Expected: FAIL because the current runtime docs still describe the skill as autonomous, omit the `data/` and `patches/` directories, and do not define unified-diff patch metadata.

- [ ] **Step 3: Rewrite the runtime docs around the v3 semantics**

```markdown
## Overview

Run a continuous ML metaoptimization campaign as a deterministic state machine across reinvocations.
This skill is not a self-scheduling daemon.
It persists state, exits, and resumes when a host runtime or user invocation re-enters it.
```

```text
{project_root}/
  ml_metaopt_campaign.yaml
  AGENTS.md
  .ml-metaopt/
    state.json
    artifacts/
      code/
      data/
      manifests/
      patches/
```

```markdown
### `MAINTAIN_BACKGROUND_POOL`

- The current proposal cycle starts on the first entry into this state for an iteration.
- Persist round bookkeeping in `proposal_cycle.ideation_rounds_by_slot`.
- Freeze `current_proposals` by setting `proposal_cycle.current_pool_frozen = true` when `SELECT_EXPERIMENT` begins.
```

```markdown
### `QUIESCE_SLOTS`

- Stop launching new work.
- Persist any finished slot output before changing slot ownership.
- Wait up to a 60-second drain window for in-flight slots to complete.
- Cancel leftovers after the 60-second drain window.
- Record cancellation reasons in state and append any mechanical patch-application outcome to `local_changeset.apply_results`.
```

```markdown
## Maintenance Lane

Code-modifying maintenance and materialization workers must emit one unified diff patch artifact.
Each patch artifact must record:
- `producer_slot_id`
- `purpose`
- `patch_path`
- `target_worktree`
```

Apply those updates to `SKILL.md`, `references/state-machine.md`, and `references/worker-lanes.md`, keeping the existing machine states and lane names unchanged.

- [ ] **Step 4: Run the targeted test to verify it passes**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_runtime_docs_define_reinvocation_and_patch_artifacts -v
```

Expected: PASS

- [ ] **Step 5: Commit the runtime-doc alignment**

```bash
git add tests/test_metaopt_validation.py SKILL.md references/state-machine.md references/worker-lanes.md
git commit -m "docs: clarify v3 runtime and worker semantics"
```

### Task 5: Document validation/runtime prerequisites and finish the full v3 sweep

**Files:**
- Create: `requirements.txt`
- Modify: `tests/test_metaopt_validation.py`
- Modify: `references/dependencies.md`
- Modify: `README.md`
- Test: `python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_readme_and_dependencies_document_runtime_prereqs -v`

- [ ] **Step 1: Write the failing prerequisites test**

```python
def test_readme_and_dependencies_document_runtime_prereqs(self) -> None:
    readme = _read_text("README.md")
    dependencies = _read_text("references/dependencies.md")

    self.assertTrue((ROOT / "requirements.txt").exists())
    self.assertIn("python3 -m pip install --user -r requirements.txt", readme)
    self.assertIn("contract-only scope", readme)
    _require_pattern(self, dependencies, r"`git`.*worktree")
    _require_pattern(self, dependencies, r"PyYAML")
    _require_pattern(self, dependencies, r"host reinvocation mechanism")
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run:

```bash
python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_readme_and_dependencies_document_runtime_prereqs -v
```

Expected: FAIL because `requirements.txt` does not exist and the current docs do not describe PyYAML or the reinvocation prerequisite.

- [ ] **Step 3: Add the dependency file and rewrite the operator-facing docs**

```text
PyYAML>=6.0,<7
```

~~~markdown
## Validation

Install the validation dependency from the repo root:

```bash
python3 -m pip install --user -r requirements.txt
```

Run the validation suite from the repo root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
~~~

```markdown
This repository is a contract-only scope for the `ml-metaoptimization` skill.
It pins the public docs, examples, and fixtures for the orchestration runtime.
It does not simulate a live Copilot host or remote queue backend.
```

```markdown
## Hard Runtime Dependencies

- GitHub Copilot agent runtime with subagent dispatch
- `git` with worktree support
- unified-diff-compatible mechanical patch application capability
- host reinvocation mechanism compatible with the `AGENTS.md` resume hook
- PyYAML for the validation suite
```

Create `requirements.txt` with the PyYAML pin, extend `README.md` with the install command and contract-only-scope note, and rewrite `references/dependencies.md` to list `git`, patch-apply capability, reinvocation, and PyYAML explicitly.

- [ ] **Step 4: Install the validation dependency and run the targeted test**

Run:

```bash
python3 -m pip install --user -r requirements.txt && python3 tests/test_metaopt_validation.py MetaoptValidationTests.test_readme_and_dependencies_document_runtime_prereqs -v
```

Expected: PASS, with pip either installing PyYAML or reporting it already satisfied.

- [ ] **Step 5: Run the full validation suite and a stale-token sweep**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' && \
rg -n "max_batch_retries|ideation_rounds_this_cycle" \
  SKILL.md references README.md ml_metaopt_campaign.example.yaml tests
```

Expected:
- `unittest` ends with `OK`
- `rg` prints no matches

- [ ] **Step 6: Commit the final v3 cleanup**

```bash
git add requirements.txt README.md references/dependencies.md tests/test_metaopt_validation.py
git commit -m "docs: document v3 metaopt prerequisites"
```

## Self-Review Checklist

- Spec coverage:
  - version bump and retry-policy move: Task 1
  - resumable `proposal_cycle`, richer slots, richer changeset: Task 2
  - manifest/data-manifest/retry ownership: Task 3
  - reinvocation semantics and patch-artifact rules: Task 4
  - documented prerequisites and validation flow: Task 5
- Placeholder scan:
  - the plan contains no placeholder language
- Type consistency:
  - use `proposal_cycle`, `model_class`, `requested_model`, `resolved_model`, `integration_worktree`, `patch_artifacts`, `apply_results`, `verification_notes`, and `data_manifest_uri` consistently across tasks

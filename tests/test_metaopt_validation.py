from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

import yaml


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


def _read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str) -> dict:
    with (ROOT / relative_path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read_json(relative_path: str) -> dict:
    with (ROOT / relative_path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _require_pattern(test_case: unittest.TestCase, text: str, pattern: str) -> None:
    test_case.assertRegex(text, re.compile(pattern, re.MULTILINE | re.DOTALL))


def _require_non_empty_string(value: object, message: str) -> None:
    assert isinstance(value, str) and value, message


def _require_number(value: object, message: str) -> None:
    assert isinstance(value, (int, float)) and not isinstance(value, bool), message


def _require_sha256_digest(value: object, message: str) -> None:
    assert isinstance(value, str) and SHA256_DIGEST_RE.fullmatch(value), message


def _validate_backend_payload(kind: str, payload: dict) -> None:
    assert isinstance(payload, dict), "backend payload must be an object"
    assert isinstance(payload.get("operation"), str) and payload["operation"], "operation is required"
    assert type(payload.get("exit_code")) is int, "exit_code must be an integer"

    if kind == "launch_sweep":
        assert payload.get("operation") == "launch_sweep", "launch_sweep payload must declare correct operation"
        assert isinstance(payload.get("sweep_id"), str) and payload["sweep_id"], "sweep_id is required"
        assert isinstance(payload.get("sweep_url"), str) and payload["sweep_url"], "sweep_url is required"
        assert isinstance(payload.get("sky_job_ids"), list), "sky_job_ids must be a list"
        assert isinstance(payload.get("launched_at"), str) and payload["launched_at"], "launched_at is required"
        return

    if kind == "poll_sweep":
        assert payload.get("operation") == "poll_sweep", "poll_sweep payload must declare correct operation"
        assert payload.get("sweep_status") in VALID_SWEEP_STATUSES, "invalid sweep_status"
        _require_number(payload.get("best_metric_value"), "best_metric_value must be numeric")
        assert isinstance(payload.get("best_run_id"), str) and payload["best_run_id"], "best_run_id is required"
        assert isinstance(payload.get("killed_runs"), list), "killed_runs must be a list"
        _require_number(payload.get("cumulative_spend_usd"), "cumulative_spend_usd must be numeric")
        return

    raise AssertionError(f"unsupported payload kind: {kind}")


def _validate_state_payload(payload: dict) -> None:
    required_keys = {
        "version",
        "campaign_id",
        "campaign_identity_hash",
        "status",
        "machine_state",
        "current_iteration",
        "next_action",
        "objective_snapshot",
        "proposal_cycle",
        "current_sweep",
        "selected_sweep",
        "current_proposals",
        "next_proposals",
        "baseline",
        "completed_iterations",
        "key_learnings",
        "no_improve_iterations",
        "campaign_started_at",
    }
    missing = required_keys - payload.keys()
    assert not missing, f"missing required state keys: {sorted(missing)}"
    assert payload["version"] == 4, "state fixture must use v4"
    _require_non_empty_string(payload["campaign_id"], "campaign_id is required")
    _require_sha256_digest(payload["campaign_identity_hash"], "campaign_identity_hash must be a sha256 digest")
    assert payload["status"] in VALID_STATE_STATUSES, "invalid coarse status"
    assert payload["machine_state"] in VALID_MACHINE_STATES, "invalid machine state"
    assert type(payload["current_iteration"]) is int and payload["current_iteration"] > 0, (
        "current_iteration must be a positive integer"
    )

    objective_snapshot = payload["objective_snapshot"]
    assert isinstance(objective_snapshot, dict), "objective_snapshot must be an object"
    _require_non_empty_string(objective_snapshot.get("metric"), "objective_snapshot.metric is required")
    assert objective_snapshot.get("direction") in {"minimize", "maximize"}, (
        "objective_snapshot.direction must be minimize or maximize"
    )
    _require_number(
        objective_snapshot.get("improvement_threshold"),
        "objective_snapshot.improvement_threshold must be numeric",
    )

    assert "proposal_cycle" in payload, "proposal_cycle is required"
    proposal_cycle = payload["proposal_cycle"]
    assert isinstance(proposal_cycle, dict), "proposal_cycle must be an object"
    assert isinstance(proposal_cycle.get("cycle_id"), str) and proposal_cycle["cycle_id"], "proposal_cycle.cycle_id is required"
    assert isinstance(proposal_cycle.get("current_pool_frozen"), bool), "proposal_cycle.current_pool_frozen is required"

    if payload["status"] == "RUNNING":
        assert payload["machine_state"] not in TERMINAL_MACHINE_STATES, "running state cannot point at a terminal machine state"
    else:
        assert payload["machine_state"] == payload["status"], "terminal status must mirror machine_state"
        assert payload["current_sweep"] is None, "terminal states must have current_sweep = null"

    # current_sweep: None in terminal states, object when a sweep is active
    current_sweep = payload["current_sweep"]
    if current_sweep is not None:
        assert isinstance(current_sweep, dict), "current_sweep must be an object"
        _require_non_empty_string(current_sweep.get("sweep_id"), "current_sweep.sweep_id is required")
        _require_non_empty_string(current_sweep.get("sweep_url"), "current_sweep.sweep_url is required")
        assert isinstance(current_sweep.get("sky_job_ids"), list), "current_sweep.sky_job_ids must be a list"
        _require_non_empty_string(current_sweep.get("launched_at"), "current_sweep.launched_at is required")
        _require_number(current_sweep.get("cumulative_spend_usd"), "current_sweep.cumulative_spend_usd must be numeric")

    # selected_sweep: None in terminal states (and pre-selection states), object after SELECT_AND_DESIGN_SWEEP
    selected_sweep = payload["selected_sweep"]
    if selected_sweep is not None:
        assert isinstance(selected_sweep, dict), "selected_sweep must be an object"
        _require_non_empty_string(
            selected_sweep.get("proposal_id"),
            "selected_sweep.proposal_id is required",
        )

    if payload["machine_state"] in TERMINAL_MACHINE_STATES:
        assert payload["current_sweep"] is None, "terminal states must have current_sweep = null"

    assert isinstance(payload["current_proposals"], list), "current_proposals must be a list"
    for proposal in payload["current_proposals"]:
        assert isinstance(proposal, dict), "current_proposals entries must be objects"

    assert isinstance(payload["next_proposals"], list), "next_proposals must be a list"
    for proposal in payload["next_proposals"]:
        assert isinstance(proposal, dict), "next_proposals entries must be objects"

    baseline = payload["baseline"]
    assert isinstance(baseline, dict), "baseline must be an object"
    _require_non_empty_string(baseline.get("metric"), "baseline.metric is required")
    _require_number(baseline.get("value"), "baseline.value must be numeric")

    completed_iterations = payload["completed_iterations"]
    assert isinstance(completed_iterations, list), "completed_iterations must be a list"
    for completed_iteration in completed_iterations:
        assert isinstance(completed_iteration, dict), "completed_iterations entries must be objects"
        assert type(completed_iteration.get("iteration")) is int and completed_iteration["iteration"] > 0, (
            "completed_iterations entries must include positive iteration"
        )
        _require_number(
            completed_iteration.get("best_metric_value"),
            "completed_iterations best_metric_value must be numeric",
        )

    key_learnings = payload["key_learnings"]
    assert isinstance(key_learnings, list), "key_learnings must be a list"
    for learning in key_learnings:
        _require_non_empty_string(learning, "key_learnings entries must be non-empty strings")

    assert type(payload["no_improve_iterations"]) is int and payload["no_improve_iterations"] >= 0, (
        "no_improve_iterations must be a non-negative integer"
    )


class MetaoptValidationTests(unittest.TestCase):
    def test_example_campaign_has_required_v4_top_level_keys(self) -> None:
        campaign = _read_yaml("ml_metaopt_campaign.example.yaml")

        required_top_level = {
            "campaign",
            "project",
            "wandb",
            "compute",
            "objective",
            "proposal_policy",
            "stop_conditions",
        }
        for key in required_top_level:
            self.assertIn(key, campaign, f"ml_metaopt_campaign.example.yaml must have top-level key '{key}'")

        for removed_key in ("datasets", "dispatch_policy", "sanity", "artifacts", "remote_queue", "execution"):
            self.assertNotIn(removed_key, campaign, f"v4 campaign must not have v3 key '{removed_key}'")

        compute = campaign.get("compute", {})
        for compute_key in ("provider", "accelerator", "num_sweep_agents", "idle_timeout_minutes", "max_budget_usd"):
            self.assertIn(compute_key, compute, f"compute must have key '{compute_key}'")

        wandb = campaign.get("wandb", {})
        for wandb_key in ("entity", "project"):
            self.assertIn(wandb_key, wandb, f"wandb must have key '{wandb_key}'")

        objective = campaign.get("objective", {})
        for obj_key in ("metric", "direction", "improvement_threshold"):
            self.assertIn(obj_key, objective, f"objective must have key '{obj_key}'")

    def test_backend_contract_defines_stdout_json_wire_format(self) -> None:
        contract = _read_text("references/backend-contract.md")

        _require_pattern(
            self,
            contract,
            r"## Enqueue Contract.*stdout JSON object.*`batch_id`.*`queue_ref`.*`status`",
        )
        _require_pattern(
            self,
            contract,
            r"## Status Contract.*stdout JSON object.*`batch_id`.*`status`.*`timestamps`",
        )
        _require_pattern(
            self,
            contract,
            r"## Results Contract.*stdout JSON object.*`best_aggregate_result`.*`per_dataset`.*`artifact_locations`.*`logs_location`",
        )

    def test_hash_canonicalization_rules_are_explicit(self) -> None:
        contracts = _read_text("references/contracts.md")
        for required_detail in [
            "sorted keys",
            '(",", ":")',
            "ensure_ascii",
            "UTF-8",
        ]:
            self.assertIn(
                required_detail,
                contracts,
                f"contracts.md hash canonicalization must specify: {required_detail}",
            )

    def test_hash_contract_and_safe_resume_behavior_are_documented(self) -> None:
        contracts = _read_text("references/contracts.md")
        machine = _read_text("references/state-machine.md")

        self.assertIn("`campaign_identity_hash`", contracts)
        self.assertIn("`runtime_config_hash`", contracts)
        self.assertNotIn("`campaign_hash`", contracts)
        _require_pattern(
            self,
            machine,
            r"campaign identity hash mismatch.*`BLOCKED_CONFIG`.*archive or remove the stale state",
        )

    def test_state_machine_bounds_sanity_and_quiesces_slots(self) -> None:
        machine = _read_text("references/state-machine.md")
        skill = _read_text("SKILL.md")

        for state_name in ("`DESIGN_EXPERIMENT`", "`QUIESCE_SLOTS`"):
            self.assertIn(state_name, machine)
            self.assertIn(state_name, skill)

        _require_pattern(self, machine, r"### `LOCAL_SANITY`.*maximum 3 remediation attempts")
        _require_pattern(self, skill, r"LOCAL_SANITY.*max 3 attempts")
        _require_pattern(self, machine, r"### `QUIESCE_SLOTS`.*60-second drain window.*cancel leftovers")
        _require_pattern(
            self,
            machine,
            r"### Terminal States.*`COMPLETE`:.*all slots have already been drained or canceled",
        )

    def test_contracts_define_slot_model_and_iteration_reporting(self) -> None:
        contracts = _read_text("references/contracts.md")
        machine = _read_text("references/state-machine.md")

        _require_pattern(
            self,
            contracts,
            r"## Slot Contract.*`slot_class`.*`background`.*`auxiliary`.*`mode`.*`materialization`",
        )
        _require_pattern(
            self,
            machine,
            r"`objective\.improvement_threshold`.*reset `no_improve_iterations` to `0`",
        )
        _require_pattern(
            self,
            contracts,
            r"At the end of `ROLL_ITERATION`, after carry-over filtering is complete, emit",
        )

    def test_contracts_define_state_lifecycle_nullability_and_materialization_worker_pairing(self) -> None:
        contracts = _read_text("references/contracts.md")

        _require_pattern(
            self,
            contracts,
            r"## Selected Experiment Contract.*`selected_experiment` may be `null` until `SELECT_EXPERIMENT` persists a winner.*authoritative handoff object",
        )
        _require_pattern(
            self,
            contracts,
            r"## Local Changeset Contract.*`local_changeset` may be `null` until `MATERIALIZE_CHANGESET` persists outputs; once present, it is an object with the documented fields",
        )
        _require_pattern(
            self,
            contracts,
            r"`mode = materialization` requires `model_class = strong_coder`",
        )

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
            r"## Local Changeset Contract.*`integration_worktree`.*`patch_artifacts`.*`apply_results`.*`data_manifest_uri`",
        )
        _require_pattern(
            self,
            contracts,
            r"## Local Changeset Contract.*`patch_artifacts\[\]`.*`producer_slot_id`.*`purpose`.*`patch_path`.*`target_worktree`",
        )
        _require_pattern(
            self,
            contracts,
            r"## Local Changeset Contract.*`apply_results\[\]`.*`patch_path`.*`status`",
        )
        _require_pattern(
            self,
            contracts,
            r"## State File.*`remote_batches\[\]`.*`batch_id`.*`queue_ref`.*`status`",
        )
        _require_pattern(
            self,
            contracts,
            r"## Batch Manifest Contract.*`version`.*`campaign_id`.*`iteration`.*`batch_id`.*`experiment`.*`retry_policy`.*`artifacts\.code_artifact\.uri`.*`artifacts\.data_manifest\.uri`.*`execution\.entrypoint`",
        )
        _require_pattern(
            self,
            backend,
            r"## Retry Policy Contract.*backend.*must honor the declared retry policy",
        )

    def test_runtime_docs_define_reinvocation_and_patch_artifacts(self) -> None:
        skill = _read_text("SKILL.md")
        machine = _read_text("references/state-machine.md")
        lanes = _read_text("references/worker-lanes.md")

        _require_pattern(self, skill, r"not a self-scheduling daemon")
        _require_pattern(self, skill, r"persists state, exits, and resumes")
        _require_pattern(
            self,
            skill,
            r"host runtime or user invocation re-enters it",
        )
        _require_pattern(self, skill, r"artifacts/.*code/.*data/.*manifests/.*patches/")
        _require_pattern(
            self,
            skill,
            r"`SELECT_EXPERIMENT` begins.*freeze|freeze.*when `SELECT_EXPERIMENT` begins",
        )
        _require_pattern(
            self,
            machine,
            r"`proposal_cycle`.*`ideation_rounds_by_slot`.*floor rule",
        )
        _require_pattern(
            self,
            machine,
            r"### `MAINTAIN_BACKGROUND_POOL`.*Create or reset `proposal_cycle\.cycle_id` when a new iteration first enters this state after `ROLL_ITERATION` or fresh initialization.*### `SELECT_EXPERIMENT`.*keep `proposal_cycle\.cycle_id` stable for auditability until the next iteration resets it",
        )
        _require_pattern(
            self,
            machine,
            r"### `MAINTAIN_BACKGROUND_POOL`.*Set `proposal_cycle\.current_pool_frozen = false` when a new proposal cycle begins.*### `SELECT_EXPERIMENT`.*setting `proposal_cycle\.current_pool_frozen = true` once selection starts",
        )
        _require_pattern(
            self,
            machine,
            r"### `MAINTAIN_BACKGROUND_POOL`.*Clear `proposal_cycle\.shortfall_reason` when a new cycle begins.*### `WAIT_FOR_PROPOSAL_THRESHOLD`.*set `proposal_cycle\.shortfall_reason` to the current blocking reason.*Clear `proposal_cycle\.shortfall_reason` once progress is allowed into `SELECT_EXPERIMENT`",
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

    def test_readme_documents_validation_command_and_agent_manifest(self) -> None:
        readme = _read_text("README.md")

        self.assertIn("python3 -m unittest discover -s tests", readme)
        self.assertIn("`agents/openai.yaml`", readme)
        _require_pattern(self, readme, r"OpenAI|Codex")

    def test_readme_and_dependencies_document_runtime_prereqs(self) -> None:
        readme = _read_text("README.md")
        dependencies = _read_text("references/dependencies.md")

        self.assertTrue((ROOT / "requirements.txt").exists())
        self.assertIn("python3 -m pip install --user -r requirements.txt", readme)
        self.assertIn("contract-only scope", readme)
        _require_pattern(self, dependencies, r"`git`.*worktree")
        _require_pattern(self, dependencies, r"PyYAML")
        _require_pattern(self, dependencies, r"host reinvocation mechanism")
        _require_pattern(
            self,
            dependencies,
            r"`ml_metaopt_campaign\.yaml`.*`AGENTS\.md`.*`\.ml-metaopt/state\.json`.*created on first run if absent",
        )
        _require_pattern(
            self,
            dependencies,
            r"`SKILL\.md`.*`references/contracts\.md`.*`references/state-machine\.md`.*"
            r"`references/worker-lanes\.md`.*`references/dispatch-guide\.md`.*"
            r"`references/backend-contract\.md`.*"
            r"`ml_metaopt_campaign\.example\.yaml`",
        )

    def test_backend_and_state_fixtures_validate(self) -> None:
        _validate_backend_payload("launch_sweep", _read_json("tests/fixtures/backend/launch-sweep-valid.json"))
        _validate_backend_payload("poll_sweep", _read_json("tests/fixtures/backend/poll-sweep-completed.json"))
        _validate_backend_payload("poll_sweep", _read_json("tests/fixtures/backend/poll-sweep-running.json"))
        _validate_backend_payload("poll_sweep", _read_json("tests/fixtures/backend/poll-sweep-budget-exceeded.json"))
        _validate_state_payload(_read_json("tests/fixtures/state/running.json"))
        _validate_state_payload(_read_json("tests/fixtures/state/complete.json"))

    def test_v4_state_fixtures_have_sweep_fields_and_proposal_cycle(self) -> None:
        running = _read_json("tests/fixtures/state/running.json")
        complete = _read_json("tests/fixtures/state/complete.json")

        for fixture in (running, complete):
            self.assertEqual(fixture["version"], 4)
            self.assertIn("proposal_cycle", fixture)
            self.assertIn("current_sweep", fixture)
            self.assertIn("selected_sweep", fixture)

        # running fixture must have a non-null current_sweep
        self.assertIsNotNone(running["current_sweep"])
        self.assertIn("sweep_id", running["current_sweep"])
        self.assertIn("sky_job_ids", running["current_sweep"])

        # complete fixture must have null current_sweep (terminal state)
        self.assertIsNone(complete["current_sweep"])
        self.assertIsNone(complete["selected_sweep"])

    def test_invalid_fixtures_are_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            _validate_state_payload(_read_json("tests/fixtures/state/invalid-status-pair.json"))

        with self.assertRaisesRegex(AssertionError, r"missing required state keys.*proposal_cycle"):
            _validate_state_payload(_read_json("tests/fixtures/state/invalid-missing-proposal-cycle.json"))

    def test_launch_sweep_payload_rejects_missing_sweep_id(self) -> None:
        fixture = dict(_read_json("tests/fixtures/backend/launch-sweep-valid.json"))

        missing_sweep_id = dict(fixture)
        del missing_sweep_id["sweep_id"]
        with self.assertRaisesRegex(AssertionError, r"sweep_id is required"):
            _validate_backend_payload("launch_sweep", missing_sweep_id)

        blank_sweep_url = dict(fixture)
        blank_sweep_url["sweep_url"] = ""
        with self.assertRaisesRegex(AssertionError, r"sweep_url is required"):
            _validate_backend_payload("launch_sweep", blank_sweep_url)

    def test_poll_sweep_payload_rejects_invalid_status(self) -> None:
        fixture = dict(_read_json("tests/fixtures/backend/poll-sweep-running.json"))

        invalid_status = dict(fixture)
        invalid_status["sweep_status"] = "pending"
        with self.assertRaisesRegex(AssertionError, r"invalid sweep_status"):
            _validate_backend_payload("poll_sweep", invalid_status)

        boolean_metric = dict(fixture)
        boolean_metric["best_metric_value"] = True
        with self.assertRaisesRegex(AssertionError, r"best_metric_value must be numeric"):
            _validate_backend_payload("poll_sweep", boolean_metric)

    def test_state_validator_rejects_malformed_nested_sections_with_clear_messages(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        malformed_proposal_cycle = dict(fixture)
        malformed_proposal_cycle["proposal_cycle"] = []
        with self.assertRaisesRegex(AssertionError, r"proposal_cycle must be an object"):
            _validate_state_payload(malformed_proposal_cycle)

        malformed_current_sweep = dict(fixture)
        malformed_current_sweep["current_sweep"] = []
        with self.assertRaisesRegex(AssertionError, r"current_sweep must be an object"):
            _validate_state_payload(malformed_current_sweep)

        malformed_selected_sweep = dict(fixture)
        malformed_selected_sweep["selected_sweep"] = []
        with self.assertRaisesRegex(AssertionError, r"selected_sweep must be an object"):
            _validate_state_payload(malformed_selected_sweep)

    def test_state_validator_rejects_malformed_baseline_and_selected_sweep_shapes(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        boolean_baseline_value = copy.deepcopy(fixture)
        boolean_baseline_value["baseline"]["value"] = True
        with self.assertRaisesRegex(AssertionError, r"baseline.value must be numeric"):
            _validate_state_payload(boolean_baseline_value)

        blank_baseline_metric = copy.deepcopy(fixture)
        blank_baseline_metric["baseline"] = dict(fixture["baseline"])
        blank_baseline_metric["baseline"]["metric"] = ""
        with self.assertRaisesRegex(AssertionError, r"baseline.metric is required"):
            _validate_state_payload(blank_baseline_metric)

        malformed_selected_sweep = copy.deepcopy(fixture)
        malformed_selected_sweep["selected_sweep"] = []
        with self.assertRaisesRegex(AssertionError, r"selected_sweep must be an object"):
            _validate_state_payload(malformed_selected_sweep)

        blank_selected_sweep_proposal = copy.deepcopy(fixture)
        blank_selected_sweep_proposal["selected_sweep"] = {"proposal_id": ""}
        with self.assertRaisesRegex(AssertionError, r"selected_sweep.proposal_id is required"):
            _validate_state_payload(blank_selected_sweep_proposal)

    def test_state_validator_enforces_terminal_current_sweep_null_invariant(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        # Running state with current_sweep is valid
        _validate_state_payload(fixture)

        # Terminal state with null current_sweep is valid
        complete = _read_json("tests/fixtures/state/complete.json")
        self.assertIsNone(complete["current_sweep"])
        _validate_state_payload(complete)

        # Terminal state with non-null current_sweep must be rejected
        bad_terminal = copy.deepcopy(complete)
        bad_terminal["current_sweep"] = fixture["current_sweep"]
        with self.assertRaisesRegex(AssertionError, r"terminal states must have current_sweep = null"):
            _validate_state_payload(bad_terminal)

        # Blocked-config terminal with null current_sweep is valid
        blocked_config = copy.deepcopy(fixture)
        blocked_config["status"] = "BLOCKED_CONFIG"
        blocked_config["machine_state"] = "BLOCKED_CONFIG"
        blocked_config["current_sweep"] = None
        _validate_state_payload(blocked_config)

        # Blocked-protocol terminal with null current_sweep is valid
        blocked_protocol = copy.deepcopy(fixture)
        blocked_protocol["status"] = "BLOCKED_PROTOCOL"
        blocked_protocol["machine_state"] = "BLOCKED_PROTOCOL"
        blocked_protocol["current_sweep"] = None
        _validate_state_payload(blocked_protocol)

    def test_state_validator_rejects_invalid_current_sweep_shape(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        missing_sweep_id = copy.deepcopy(fixture)
        missing_sweep_id["current_sweep"] = dict(fixture["current_sweep"])
        del missing_sweep_id["current_sweep"]["sweep_id"]
        with self.assertRaisesRegex(AssertionError, r"current_sweep.sweep_id is required"):
            _validate_state_payload(missing_sweep_id)

        boolean_spend = copy.deepcopy(fixture)
        boolean_spend["current_sweep"] = dict(fixture["current_sweep"])
        boolean_spend["current_sweep"]["cumulative_spend_usd"] = True
        with self.assertRaisesRegex(AssertionError, r"current_sweep.cumulative_spend_usd must be numeric"):
            _validate_state_payload(boolean_spend)

    def test_state_validator_rejects_invalid_hash_format(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        invalid_campaign_hash = copy.deepcopy(fixture)
        invalid_campaign_hash["campaign_identity_hash"] = "sha256:ABC123"
        with self.assertRaisesRegex(AssertionError, r"campaign_identity_hash must be a sha256 digest"):
            _validate_state_payload(invalid_campaign_hash)

        short_hash = copy.deepcopy(fixture)
        short_hash["campaign_identity_hash"] = "sha256:abcdef"
        with self.assertRaisesRegex(AssertionError, r"campaign_identity_hash must be a sha256 digest"):
            _validate_state_payload(short_hash)

    def test_state_validator_rejects_malformed_completed_iterations(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        malformed_completed_iterations = copy.deepcopy(fixture)
        malformed_completed_iterations["completed_iterations"] = {}
        with self.assertRaisesRegex(AssertionError, r"completed_iterations must be a list"):
            _validate_state_payload(malformed_completed_iterations)

        non_object_completed_iteration = copy.deepcopy(fixture)
        non_object_completed_iteration["completed_iterations"] = ["iter-1"]
        with self.assertRaisesRegex(AssertionError, r"completed_iterations entries must be objects"):
            _validate_state_payload(non_object_completed_iteration)

        invalid_iteration_number = copy.deepcopy(fixture)
        invalid_iteration_number["completed_iterations"] = [{"iteration": 0, "best_metric_value": 0.9}]
        with self.assertRaisesRegex(
            AssertionError, r"completed_iterations entries must include positive iteration"
        ):
            _validate_state_payload(invalid_iteration_number)

        boolean_metric = copy.deepcopy(fixture)
        boolean_metric["completed_iterations"] = [{"iteration": 1, "best_metric_value": True}]
        with self.assertRaisesRegex(AssertionError, r"completed_iterations best_metric_value must be numeric"):
            _validate_state_payload(boolean_metric)

    def test_state_validator_rejects_invalid_required_top_level_field_shapes(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        blank_campaign_id = copy.deepcopy(fixture)
        blank_campaign_id["campaign_id"] = ""
        with self.assertRaisesRegex(AssertionError, r"campaign_id is required"):
            _validate_state_payload(blank_campaign_id)

        invalid_current_iteration = copy.deepcopy(fixture)
        invalid_current_iteration["current_iteration"] = True
        with self.assertRaisesRegex(AssertionError, r"current_iteration must be a positive integer"):
            _validate_state_payload(invalid_current_iteration)

        blank_objective_metric = copy.deepcopy(fixture)
        blank_objective_metric["objective_snapshot"] = dict(fixture["objective_snapshot"])
        blank_objective_metric["objective_snapshot"]["metric"] = ""
        with self.assertRaisesRegex(AssertionError, r"objective_snapshot.metric is required"):
            _validate_state_payload(blank_objective_metric)

        invalid_objective_direction = copy.deepcopy(fixture)
        invalid_objective_direction["objective_snapshot"] = dict(fixture["objective_snapshot"])
        invalid_objective_direction["objective_snapshot"]["direction"] = "sideways"
        with self.assertRaisesRegex(AssertionError, r"objective_snapshot.direction must be minimize or maximize"):
            _validate_state_payload(invalid_objective_direction)

        invalid_objective_threshold = copy.deepcopy(fixture)
        invalid_objective_threshold["objective_snapshot"] = dict(fixture["objective_snapshot"])
        invalid_objective_threshold["objective_snapshot"]["improvement_threshold"] = True
        with self.assertRaisesRegex(AssertionError, r"objective_snapshot.improvement_threshold must be numeric"):
            _validate_state_payload(invalid_objective_threshold)

        invalid_current_proposals = copy.deepcopy(fixture)
        invalid_current_proposals["current_proposals"] = ["proposal"]
        with self.assertRaisesRegex(AssertionError, r"current_proposals entries must be objects"):
            _validate_state_payload(invalid_current_proposals)

        invalid_next_proposals = copy.deepcopy(fixture)
        invalid_next_proposals["next_proposals"] = [123]
        with self.assertRaisesRegex(AssertionError, r"next_proposals entries must be objects"):
            _validate_state_payload(invalid_next_proposals)

        invalid_key_learnings = copy.deepcopy(fixture)
        invalid_key_learnings["key_learnings"] = [""]
        with self.assertRaisesRegex(AssertionError, r"key_learnings entries must be non-empty strings"):
            _validate_state_payload(invalid_key_learnings)

        invalid_no_improve_iterations = copy.deepcopy(fixture)
        invalid_no_improve_iterations["no_improve_iterations"] = -1
        with self.assertRaisesRegex(AssertionError, r"no_improve_iterations must be a non-negative integer"):
            _validate_state_payload(invalid_no_improve_iterations)

    def test_v4_state_version_required(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        wrong_version = copy.deepcopy(fixture)
        wrong_version["version"] = 3
        with self.assertRaisesRegex(AssertionError, r"state fixture must use v4"):
            _validate_state_payload(wrong_version)

    # ------------------------------------------------------------------
    # Cross-document worker target consistency
    # ------------------------------------------------------------------

    def test_worker_skill_names_consistent_across_all_reference_docs(self) -> None:
        """Every worker target in the Worker Skills table must appear in
        worker-lanes.md, state-machine.md, dispatch-guide.md, and dependencies.md."""
        skill_md = _read_text("SKILL.md")
        worker_lanes = _read_text("references/worker-lanes.md")
        state_machine = _read_text("references/state-machine.md")
        dispatch_guide = _read_text("references/dispatch-guide.md")
        dependencies = _read_text("references/dependencies.md")

        expected_skills = [
            "metaopt-ideation-worker",
            "metaopt-selection-worker",
            "metaopt-design-worker",
            "metaopt-materialization-worker",
            "metaopt-diagnosis-worker",
            "metaopt-analysis-worker",
            "metaopt-rollover-worker",
            "repo-audit-refactor-optimize",
        ]

        for skill_name in expected_skills:
            with self.subTest(skill=skill_name):
                self.assertIn(
                    skill_name, skill_md,
                    f"{skill_name} missing from SKILL.md"
                )
                self.assertIn(
                    skill_name, worker_lanes,
                    f"{skill_name} missing from worker-lanes.md"
                )
                self.assertIn(
                    skill_name, state_machine,
                    f"{skill_name} missing from state-machine.md"
                )
                self.assertIn(
                    skill_name, dispatch_guide,
                    f"{skill_name} missing from dispatch-guide.md"
                )
                self.assertIn(
                    skill_name, dependencies,
                    f"{skill_name} missing from dependencies.md"
                )

    def test_dispatch_guide_covers_all_dispatch_states(self) -> None:
        """The dispatch guide must document every state that dispatches a worker."""
        dispatch_guide = _read_text("references/dispatch-guide.md")

        dispatch_states = [
            "MAINTAIN_BACKGROUND_POOL",
            "SELECT_EXPERIMENT",
            "DESIGN_EXPERIMENT",
            "MATERIALIZE_CHANGESET",
            "LOCAL_SANITY",
            "ANALYZE_RESULTS",
            "ROLL_ITERATION",
        ]

        for state in dispatch_states:
            with self.subTest(state=state):
                self.assertIn(
                    state, dispatch_guide,
                    f"{state} missing from dispatch-guide.md"
                )

    def test_dispatch_guide_listed_in_required_references(self) -> None:
        """SKILL.md Required References must include dispatch-guide.md."""
        skill_md = _read_text("SKILL.md")
        self.assertIn("references/dispatch-guide.md", skill_md)

    def test_worker_lanes_has_rollover_lane(self) -> None:
        """worker-lanes.md must document the rollover lane."""
        worker_lanes = _read_text("references/worker-lanes.md")
        self.assertIn("## Rollover Lane", worker_lanes)
        self.assertIn("metaopt-rollover-worker", worker_lanes)

    def test_contracts_documents_inline_dispatch(self) -> None:
        """contracts.md must distinguish slot-based from inline dispatch."""
        contracts = _read_text("references/contracts.md")
        self.assertIn("Inline dispatch", contracts)
        self.assertIn("Slot-based dispatch", contracts)

    def test_skill_availability_section_exists(self) -> None:
        """SKILL.md must document degradation behavior for missing worker skills."""
        skill_md = _read_text("SKILL.md")
        self.assertIn("## Skill Availability", skill_md)
        for skill_name in [
            "metaopt-materialization-worker",
            "metaopt-ideation-worker",
            "metaopt-rollover-worker",
            "repo-audit-refactor-optimize",
        ]:
            self.assertIn(skill_name, skill_md.split("## Skill Availability")[1].split("## Common Mistakes")[0],
                          f"{skill_name} not in Skill Availability section")

    def test_delegation_list_includes_all_worker_skills(self) -> None:
        """SKILL.md delegation list must reference the ideation worker and rollover."""
        skill_md = _read_text("SKILL.md")
        delegation_section = skill_md.split("The orchestrator must delegate all semantic decisions.")[1].split("## Quick Flow")[0]
        self.assertIn("metaopt-ideation-worker", delegation_section)
        self.assertIn("metaopt-rollover-worker", delegation_section)

    def test_proposal_record_shape_documented(self) -> None:
        """contracts.md must define proposal record shape with orchestrator-owned and worker-provided fields."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(self, contracts, r"### Proposal Record Shape")
        _require_pattern(self, contracts, r"proposal_id.*non-empty string.*unique within the campaign")
        _require_pattern(self, contracts, r"source_slot_id.*non-empty string")
        _require_pattern(self, contracts, r"creation_iteration.*positive integer")
        _require_pattern(self, contracts, r"created_at.*ISO 8601")
        _require_pattern(self, contracts, r"[Ll]eaf workers never generate `proposal_id`")

    def test_selected_experiment_expanded_shape(self) -> None:
        """selected_experiment must document all lifecycle fields from SELECT through ANALYZE."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(self, contracts, r"proposal_snapshot.*frozen copy")
        _require_pattern(self, contracts, r"selection_rationale.*string")
        _require_pattern(self, contracts, r"design.*object or `null`.*authoritative input for MATERIALIZE")
        _require_pattern(self, contracts, r"diagnosis_history.*array.*ordered list")
        _require_pattern(self, contracts, r"analysis_summary.*object or `null`.*structured analysis")
        _require_pattern(self, contracts, r"clears `selected_experiment`.*ROLL_ITERATION")

    def test_dispatch_guide_has_prompt_envelope(self) -> None:
        """dispatch-guide.md must define the normalized prompt envelope."""
        guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, guide, r"## Prompt Envelope")
        _require_pattern(self, guide, r"campaign_id.*string.*campaign.campaign_id")
        _require_pattern(self, guide, r"aggregation_method.*string")
        _require_pattern(self, guide, r"aggregation_weights.*object or null")
        _require_pattern(self, guide, r"trial_budget.*object")
        _require_pattern(self, guide, r"search_strategy.*object")

    def test_remediation_flow_documented(self) -> None:
        """state-machine.md must document the three-way diagnosis routing."""
        sm = _read_text("references/state-machine.md")
        _require_pattern(self, sm, r'"fix".*metaopt-materialization-worker.*remediation')
        _require_pattern(self, sm, r'"adjust_config".*BLOCKED_CONFIG')
        _require_pattern(self, sm, r'"abandon".*FAILED')

    def test_remote_failure_diagnosis_path(self) -> None:
        """Remote failures must route through diagnosis before terminal transition."""
        sm = _read_text("references/state-machine.md")
        _require_pattern(self, sm, r"WAIT_FOR_REMOTE_BATCH.*status.*failed.*metaopt-diagnosis-worker")
        guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, guide, r"WAIT_FOR_REMOTE_BATCH.*Remote Failure Diagnosis")

    def test_materialization_modes_documented(self) -> None:
        """dispatch-guide.md must document all three materialization modes."""
        guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, guide, r'materialization_mode.*"standard"')
        _require_pattern(self, guide, r'materialization_mode.*"remediation"')
        _require_pattern(self, guide, r'materialization_mode.*"conflict_resolution"')

    def test_diagnosis_action_routing_complete(self) -> None:
        """dispatch-guide.md LOCAL_SANITY section must route all three diagnosis actions."""
        guide = _read_text("references/dispatch-guide.md")
        local_sanity_section = guide.split("## LOCAL_SANITY")[1].split("## WAIT_FOR_REMOTE")[0]
        self.assertIn('"fix"', local_sanity_section)
        self.assertIn('"adjust_config"', local_sanity_section)
        self.assertIn('"abandon"', local_sanity_section)
        self.assertIn("BLOCKED_CONFIG", local_sanity_section)
        self.assertIn("FAILED", local_sanity_section)

    def test_runtime_capabilities_in_state_schema(self) -> None:
        """contracts.md state file must include runtime_capabilities with skill verification fields."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(self, contracts, r"runtime_capabilities")
        _require_pattern(self, contracts, r"verified_at.*ISO 8601")
        _require_pattern(self, contracts, r"available_skills.*array")
        _require_pattern(self, contracts, r"missing_skills.*array")
        _require_pattern(self, contracts, r"degraded_lanes.*array")

    def test_conflict_resolution_routes_through_materialization(self) -> None:
        """Conflict resolution must route through metaopt-materialization-worker, not unnamed strong_coder."""
        lanes = _read_text("references/worker-lanes.md")
        _require_pattern(self, lanes, r"conflict.*metaopt-materialization-worker")
        skill_md = _read_text("SKILL.md")
        _require_pattern(self, skill_md, r"conflict resolution.*metaopt-materialization-worker")

    def test_dispatch_guide_enrichment_step(self) -> None:
        """dispatch-guide.md ideation output must document proposal enrichment by orchestrator."""
        guide = _read_text("references/dispatch-guide.md")
        ideation_section = guide.split("## MAINTAIN_BACKGROUND_POOL — Ideation")[1].split("## MAINTAIN_BACKGROUND_POOL — Maintenance")[0]
        self.assertIn("proposal_id", ideation_section)
        self.assertIn("source_slot_id", ideation_section)
        self.assertIn("creation_iteration", ideation_section)
        self.assertIn("created_at", ideation_section)

    # --- Control Protocol tests ---

    def test_control_protocol_reference_exists(self) -> None:
        """references/control-protocol.md must exist and be non-trivial."""
        protocol = _read_text("references/control-protocol.md")
        self.assertGreater(len(protocol), 200, "control-protocol.md is too short to be meaningful")

    def test_control_protocol_defines_handoff_envelope(self) -> None:
        """control-protocol.md must define the universal control-handoff envelope with all required fields."""
        protocol = _read_text("references/control-protocol.md")
        required_fields = [
            "handoff_type",
            "control_agent",
            "recommended_next_machine_state",
            "launch_requests",
            "state_patch",
            "pre_launch_directives",
            "post_launch_directives",
            "summary",
            "warnings",
        ]
        for field in required_fields:
            self.assertIn(field, protocol, f"control-protocol.md must define envelope field '{field}'")

    def test_control_protocol_lists_all_control_agents(self) -> None:
        """control-protocol.md must reference all seven control agents."""
        protocol = _read_text("references/control-protocol.md")
        control_agents = [
            "metaopt-load-campaign",
            "metaopt-hydrate-state",
            "metaopt-background-control",
            "metaopt-select-design",
            "metaopt-local-execution-control",
            "metaopt-remote-execution-control",
            "metaopt-iteration-close-control",
        ]
        for agent in control_agents:
            self.assertIn(agent, protocol, f"control-protocol.md must reference control agent '{agent}'")

    def test_control_protocol_defines_state_patch_ownership(self) -> None:
        """control-protocol.md must define state-patch ownership rules mapping control agents to state keys."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"[Oo]wnership")
        # Each control agent should have at least one owned state key documented
        _require_pattern(self, protocol, r"metaopt-load-campaign.*campaign_identity_hash|campaign_identity_hash.*metaopt-load-campaign")
        _require_pattern(self, protocol, r"metaopt-hydrate-state.*runtime_capabilities|runtime_capabilities.*metaopt-hydrate-state")

    def test_skill_md_references_control_protocol(self) -> None:
        """SKILL.md Required References must include references/control-protocol.md."""
        skill_md = _read_text("SKILL.md")
        self.assertIn("references/control-protocol.md", skill_md)

    def test_skill_md_describes_orchestrator_as_transport(self) -> None:
        """SKILL.md must describe the orchestrator as a transport/runtime shell with control agents as the semantic layer."""
        skill_md = _read_text("SKILL.md")
        _require_pattern(self, skill_md, r"transport|runtime shell")
        _require_pattern(self, skill_md, r"[Cc]ontrol agent")

    def test_control_protocol_cross_referenced_from_contracts(self) -> None:
        """contracts.md must cross-reference control-protocol.md."""
        contracts = _read_text("references/contracts.md")
        self.assertIn("control-protocol.md", contracts)

    def test_control_protocol_documents_plan_gate_pattern(self) -> None:
        """control-protocol.md must document the plan/gate control pattern used by control agents."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"[Pp]lan")
        _require_pattern(self, protocol, r"[Gg]ate")

    def test_control_protocol_defines_executor_directive_catalog(self) -> None:
        """control-protocol.md must define the concrete executor directive catalog."""
        protocol = _read_text("references/control-protocol.md")
        for action_name in (
            "write_manifest",
            "queue_op",
            "apply_patch_artifacts",
            "package_code_artifact",
            "package_data_manifest",
            "run_sanity",
            "emit_iteration_report",
            "drain_slots",
            "cancel_slots",
            "remove_agents_hook",
            "delete_state_file",
            "emit_final_report",
        ):
            self.assertIn(f"`{action_name}`", protocol)

    def test_control_protocol_documents_local_directive_fields(self) -> None:
        """control-protocol.md must describe the concrete fields the executor consumes for local directives."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"`apply_patch_artifacts`.*`result_file`.*`target_worktree`")
        _require_pattern(self, protocol, r"`package_code_artifact`.*`worktree`.*`code_roots`")
        _require_pattern(self, protocol, r"`package_data_manifest`.*`worktree`.*`data_roots`")
        _require_pattern(self, protocol, r"`run_sanity`.*`worktree`.*`command`.*`max_duration_seconds`")

    def test_directive_docs_require_mechanical_execution_not_inference(self) -> None:
        """Directive docs must say the orchestrator executes directives mechanically instead of inferring executor work from prose."""
        protocol = _read_text("references/control-protocol.md")
        state_machine = _read_text("references/state-machine.md")
        dispatch_guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, protocol, r"execute.*mechanically")
        _require_pattern(self, dispatch_guide, r"must not infer.*executor work")
        _require_pattern(self, state_machine, r"pre_launch_directives|post_launch_directives")

    def test_contracts_document_campaign_started_at(self) -> None:
        """contracts.md must list campaign_started_at as a required state-file key."""
        contracts = _read_text("references/contracts.md")
        self.assertIn("campaign_started_at", contracts)
        # Must appear in the required keys section, not the optional section
        required_section_end = contracts.index("Optional keys written by specific control agents")
        first_occurrence = contracts.index("campaign_started_at")
        self.assertLess(first_occurrence, required_section_end,
                        "campaign_started_at must be in the required keys section")

    def test_state_machine_documents_max_wallclock_hours_in_roll_iteration(self) -> None:
        """state-machine.md ROLL_ITERATION must document max_wallclock_hours stop condition."""
        state_machine = _read_text("references/state-machine.md")
        # The ROLL_ITERATION section should list max_wallclock_hours as a stop condition
        self.assertIn("max_wallclock_hours", state_machine)

    def test_state_machine_documents_terminal_cleanup_directives(self) -> None:
        """state-machine.md terminal states must document explicit cleanup directives."""
        state_machine = _read_text("references/state-machine.md")
        self.assertIn("pre_launch_directives", state_machine)

    def test_control_agent_manifests_reference_control_protocol(self) -> None:
        """Every control-agent manifest must reference references/control-protocol.md."""
        control_agent_manifests = [
            ".github/agents/metaopt-load-campaign.agent.md",
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-local-execution-control.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            self.assertIn(
                "references/control-protocol.md",
                content,
                f"{manifest_path} must reference the control protocol",
            )

    def test_control_agent_manifests_state_handoff_conformance(self) -> None:
        """Every control-agent manifest must state that handoff output conforms to the control protocol."""
        control_agent_manifests = [
            ".github/agents/metaopt-load-campaign.agent.md",
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-local-execution-control.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            _require_pattern(
                self,
                content,
                r"must conform.*control.handoff envelope",
            )

    def test_control_agent_manifests_declare_directives_authoritative(self) -> None:
        """Every control-agent manifest must declare directive lists as the authoritative executor input."""
        control_agent_manifests = [
            ".github/agents/metaopt-load-campaign.agent.md",
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-local-execution-control.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            self.assertIn(
                "pre_launch_directives",
                content,
                f"{manifest_path} must mention pre_launch_directives",
            )
            self.assertIn(
                "post_launch_directives",
                content,
                f"{manifest_path} must mention post_launch_directives",
            )
            _require_pattern(
                self,
                content,
                r"`pre_launch_directives`.*`post_launch_directives`.*authoritative.*executor",
            )
            _require_pattern(
                self,
                content,
                r"orchestrator.*execut(es|e).*mechanically.*in order",
            )
            _require_pattern(
                self,
                content,
                r"must not infer.*executor work.*prose|must not infer.*executor work.*summar|must not infer.*executor work.*legacy",
            )

    def test_control_agent_manifests_do_not_apply_state_in_agent_commands(self) -> None:
        """Control agents must emit handoffs only; state application is orchestrator-owned."""
        control_agent_manifests = [
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-local-execution-control.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            command_blocks = re.findall(r"```bash\n(.*?)```", content, flags=re.DOTALL)
            self.assertTrue(command_blocks, f"{manifest_path} must include executable handoff commands")
            for command in command_blocks:
                self.assertNotIn("--apply-state", command, f"{manifest_path} agent command must be emit-only")

    # ------------------------------------------------------------------
    # Preflight dependency documentation consistency
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Task 5: BLOCKED_PROTOCOL, preferred_model, preconditions, queue-only
    # ------------------------------------------------------------------

    def test_blocked_protocol_documented_as_terminal_state_in_state_machine(self) -> None:
        """state-machine.md must list BLOCKED_PROTOCOL as a terminal state with
        cleanup semantics (preserve state, remove hook)."""
        sm = _read_text("references/state-machine.md")
        self.assertIn("BLOCKED_PROTOCOL", sm)
        _require_pattern(self, sm, r"## States.*`BLOCKED_PROTOCOL`.*## ")
        _require_pattern(self, sm, r"### Terminal States.*`BLOCKED_PROTOCOL`")

    def test_blocked_protocol_in_skill_md_diagram(self) -> None:
        """SKILL.md state-machine diagram must include BLOCKED_PROTOCOL as a
        terminal node with transition edges from running states."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r'"BLOCKED_PROTOCOL".*doublecircle')
        _require_pattern(self, skill, r'-> "BLOCKED_PROTOCOL"')

    def test_blocked_protocol_select_design_edges_in_diagram(self) -> None:
        """SKILL.md diagram must have BLOCKED_PROTOCOL edges from
        SELECT_EXPERIMENT and DESIGN_EXPERIMENT for protocol breaches."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r'"SELECT_EXPERIMENT" -> "BLOCKED_PROTOCOL"')
        _require_pattern(self, skill, r'"DESIGN_EXPERIMENT" -> "BLOCKED_PROTOCOL"')

    def test_blocked_protocol_roll_iteration_edge_in_diagram(self) -> None:
        """SKILL.md diagram must have BLOCKED_PROTOCOL edge from
        ROLL_ITERATION for iteration-close protocol breach."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r'"ROLL_ITERATION" -> "BLOCKED_PROTOCOL"')

    def test_blocked_protocol_in_contracts_status_semantics(self) -> None:
        """contracts.md status semantics must pair BLOCKED_PROTOCOL status
        with BLOCKED_PROTOCOL machine_state."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(
            self,
            contracts,
            r"`status = BLOCKED_PROTOCOL` only with `machine_state = BLOCKED_PROTOCOL`",
        )

    def test_blocked_protocol_in_control_protocol(self) -> None:
        """control-protocol.md must document which control agents can emit
        BLOCKED_PROTOCOL and the fail-closed rule."""
        protocol = _read_text("references/control-protocol.md")
        self.assertIn("BLOCKED_PROTOCOL", protocol)
        _require_pattern(self, protocol, r"fail.closed.*BLOCKED_PROTOCOL|BLOCKED_PROTOCOL.*fail.closed")

    def test_semantic_fallback_forbidden_in_skill_md(self) -> None:
        """SKILL.md must explicitly forbid generic semantic fallback."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r"[Ss]emantic fallback.*forbidden|[Ff]orbidden.*semantic fallback|[Nn]ever.*improv.*unsupported.*semantic")

    def test_preferred_model_documented_in_dispatch_guide(self) -> None:
        """dispatch-guide.md must document preferred_model on launch requests
        and the claude-opus-4.6 intent for strong_reasoner/strong_coder."""
        guide = _read_text("references/dispatch-guide.md")
        self.assertIn("preferred_model", guide)
        self.assertIn("claude-opus-4.6", guide)
        _require_pattern(self, guide, r"strong_coder.*claude-opus-4\.6|claude-opus-4\.6.*strong_coder")

    def test_preferred_model_documented_in_control_protocol(self) -> None:
        """control-protocol.md launch_requests must document preferred_model
        and mention strong_coder enrichment."""
        protocol = _read_text("references/control-protocol.md")
        self.assertIn("preferred_model", protocol)
        self.assertIn("strong_coder", protocol)

    def test_strong_coder_in_guardrail_preferred_model_map(self) -> None:
        """_guardrail_utils.PREFERRED_MODEL_BY_CLASS must include strong_coder
        so materialization launches are enriched correctly."""
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from _guardrail_utils import PREFERRED_MODEL_BY_CLASS
        self.assertIn("strong_coder", PREFERRED_MODEL_BY_CLASS)
        self.assertEqual(PREFERRED_MODEL_BY_CLASS["strong_coder"], "claude-opus-4.6")

    def test_worker_artifact_preconditions_in_worker_lanes(self) -> None:
        """worker-lanes.md must document that remediation requires
        diagnosis-worker output and result judgment requires analysis-worker output."""
        lanes = _read_text("references/worker-lanes.md")
        _require_pattern(self, lanes, r"[Rr]emediation.*diagnosis.worker.*output|diagnosis.worker.*output.*precondition.*remediation")
        _require_pattern(self, lanes, r"[Rr]esult judgment.*analysis.worker.*output|analysis.worker.*output.*precondition.*result judgment")

    def test_queue_only_backend_contract_strengthened(self) -> None:
        """backend-contract.md must explicitly prohibit raw SSH, Ray, and
        cluster operations from the skill."""
        backend = _read_text("references/backend-contract.md")
        _require_pattern(self, backend, r"[Nn]o raw SSH|[Nn]ever.*raw.*SSH")
        _require_pattern(self, backend, r"[Pp]rotocol breach|[Vv]iolation")

    def test_backend_contract_blocks_direct_fallback_tools_by_name(self) -> None:
        """backend-contract.md should explicitly name the ad-hoc tools that
        must never be used as remote-execution fallbacks."""
        backend = _read_text("references/backend-contract.md")
        _require_pattern(self, backend, r"ray job submit|ray start|ray stop")
        _require_pattern(self, backend, r"scp|rsync")
        _require_pattern(self, backend, r"hcloud")

    def test_control_protocol_forbids_hand_authored_semantic_state_edits(self) -> None:
        """control-protocol.md must say the orchestrator never hand-edits
        semantic state and only applies control-agent state patches plus the
        recommended machine_state transition."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"never hand.edit|must not hand.edit|manual state")
        _require_pattern(self, protocol, r"state_patch")
        _require_pattern(self, protocol, r"recommended_next_machine_state")

    def test_skill_md_forbids_direct_project_file_edits_by_orchestrator(self) -> None:
        """SKILL.md must make direct repo edits by the orchestrator a protocol
        breach rather than an acceptable shortcut around worker lanes."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r"must not.*edit.*project files|never.*edit.*project files")
        _require_pattern(self, skill, r"materialization-worker|semantic code changes")

    def test_remote_control_manifest_forbids_raw_remote_fallbacks(self) -> None:
        """The remote control-agent manifest should explicitly forbid Ray CLI,
        SSH/SCP, and cloud-console fallbacks when queue execution has trouble."""
        manifest = _read_text(".github/agents/metaopt-remote-execution-control.agent.md")
        _require_pattern(self, manifest, r"must not.*ray|never.*ray")
        _require_pattern(self, manifest, r"ssh|scp|hcloud")

    def test_iteration_close_manifest_forbids_manual_state_closure(self) -> None:
        """The iteration-close control-agent manifest should explicitly forbid
        hand-authored rollover/terminal state edits by the orchestrator."""
        manifest = _read_text(".github/agents/metaopt-iteration-close-control.agent.md")
        _require_pattern(self, manifest, r"must not.*hand.edit|never.*hand.edit|manual state")
        _require_pattern(self, manifest, r"selected_experiment|COMPLETE|BLOCKED_PROTOCOL")

    def test_blocked_protocol_in_dispatch_guide(self) -> None:
        """dispatch-guide.md must reference BLOCKED_PROTOCOL for missing
        artifact preconditions."""
        guide = _read_text("references/dispatch-guide.md")
        self.assertIn("BLOCKED_PROTOCOL", guide)

    def test_blocked_protocol_in_skill_md_behavioral_guarantees(self) -> None:
        """SKILL.md must mention BLOCKED_PROTOCOL in behavioral guarantees
        or common mistakes."""
        skill = _read_text("SKILL.md")
        self.assertIn("BLOCKED_PROTOCOL", skill)

    def test_blocked_protocol_hook_removal_in_state_machine(self) -> None:
        """state-machine.md BLOCKED_PROTOCOL cleanup must remove the AGENTS.md hook."""
        sm = _read_text("references/state-machine.md")
        _require_pattern(self, sm, r"BLOCKED_PROTOCOL.*remove.*AGENTS\.md|BLOCKED_PROTOCOL.*hook")

    def test_preflight_dependency_documented_across_public_docs(self) -> None:
        """README, SKILL.md, and dependencies.md must all document the
        metaopt-preflight prerequisite and the readiness artifact path."""
        readme = _read_text("README.md")
        skill_md = _read_text("SKILL.md")
        dependencies = _read_text("references/dependencies.md")

        artifact_path = ".ml-metaopt/preflight-readiness.json"
        preflight_name = "metaopt-preflight"

        for doc_name, text in [
            ("README.md", readme),
            ("SKILL.md", skill_md),
            ("references/dependencies.md", dependencies),
        ]:
            with self.subTest(doc=doc_name, check="preflight_name"):
                self.assertIn(
                    preflight_name, text,
                    f"{doc_name} must mention {preflight_name}",
                )

        # The readiness artifact path must be documented in SKILL.md
        # and dependencies.md (the two operational references).
        for doc_name, text in [
            ("SKILL.md", skill_md),
            ("references/dependencies.md", dependencies),
        ]:
            with self.subTest(doc=doc_name, check="artifact_path"):
                self.assertIn(
                    artifact_path, text,
                    f"{doc_name} must mention {artifact_path}",
                )

        # dependencies.md must position preflight as a startup prerequisite
        _require_pattern(
            self,
            dependencies,
            r"[Pp]reflight.*prerequisite|[Pp]rerequisite.*preflight|[Pp]reflight.*before.*LOAD_CAMPAIGN|[Pp]reflight.*before.*orchestr",
        )

    def test_skill_md_startup_path_requires_preflight(self) -> None:
        """SKILL.md Required Files or startup path must mention the
        preflight readiness artifact so the contract does not imply the
        campaign can start without preflight."""
        skill_md = _read_text("SKILL.md")

        # The Required Files section (or a nearby startup section) must
        # reference the preflight artifact.
        required_files_onward = skill_md.split("## Required Files")[1].split("## Behavioral Guarantees")[0]
        self.assertIn(
            "preflight-readiness.json",
            required_files_onward,
            "Required Files section must reference preflight-readiness.json",
        )
        self.assertIn(
            "metaopt-preflight",
            required_files_onward,
            "Required Files section must reference metaopt-preflight as the artifact source",
        )


if __name__ == "__main__":
    unittest.main()

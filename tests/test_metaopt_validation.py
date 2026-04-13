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
            r"### `launch_sweep`.*sweep_config.*wandb_entity.*wandb_project",
        )
        _require_pattern(
            self,
            contract,
            r"### `poll_sweep`.*sweep_id.*sweep_status.*best_metric_value",
        )
        _require_pattern(
            self,
            contract,
            r"### `run_smoke_test`.*command.*exit_code.*timed_out",
        )

    def test_hash_canonicalization_rules_are_explicit(self) -> None:
        contracts = _read_text("references/contracts.md")
        for required_detail in [
            "sorted keys",
            '","',
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
        self.assertNotIn("`campaign_hash`", contracts)
        _require_pattern(
            self,
            contracts,
            r"[Ii]dentity mismatch.*BLOCKED_CONFIG|BLOCKED_CONFIG.*identity.*mismatch",
        )
        self.assertIn("identity hash mismatch", machine)

    def test_state_machine_bounds_sanity_and_quiesces_slots(self) -> None:
        machine = _read_text("references/state-machine.md")
        skill = _read_text("SKILL.md")

        for state_name in ("SELECT_AND_DESIGN_SWEEP", "ROLL_ITERATION"):
            self.assertIn(state_name, machine)
            self.assertIn(state_name, skill)

        _require_pattern(self, machine, r"LOCAL_SANITY.*60-second hard timeout")
        _require_pattern(self, skill, r"LOCAL_SANITY.*60-second hard timeout")
        _require_pattern(self, machine, r"ROLL_ITERATION.*check stop conditions")
        _require_pattern(
            self,
            machine,
            r"COMPLETE.*remove_agents_hook.*emit_final_report",
        )

    def test_contracts_define_slot_model_and_iteration_reporting(self) -> None:
        contracts = _read_text("references/contracts.md")
        machine = _read_text("references/state-machine.md")

        _require_pattern(
            self,
            contracts,
            r"improvement_threshold.*float.*> 0",
        )
        _require_pattern(
            self,
            contracts,
            r"no_improve_iterations.*integer.*>= 0",
        )
        _require_pattern(
            self,
            machine,
            r"no_improve_iterations.*COMPLETE",
        )

    def test_contracts_define_state_lifecycle_nullability_and_materialization_worker_pairing(self) -> None:
        contracts = _read_text("references/contracts.md")

        _require_pattern(
            self,
            contracts,
            r"selected_sweep.*object or null.*null until selection",
        )
        _require_pattern(
            self,
            contracts,
            r"current_sweep.*object or null.*null when no sweep",
        )
        _require_pattern(
            self,
            contracts,
            r"baseline.*object or null.*null until first improvement",
        )

    def test_contract_docs_define_v4_state_manifest_and_operations(self) -> None:
        contracts = _read_text("references/contracts.md")
        backend = _read_text("references/backend-contract.md")

        _require_pattern(
            self,
            contracts,
            r"## Section 1.*State File.*proposal_cycle.*current_pool_frozen",
        )
        _require_pattern(
            self,
            contracts,
            r"current_sweep.*sweep_id.*sweep_url.*sky_job_ids",
        )
        _require_pattern(
            self,
            contracts,
            r"selected_sweep.*proposal_id.*sweep_config",
        )
        _require_pattern(
            self,
            contracts,
            r"iteration_record.*iteration.*sweep_id.*best_metric_value.*spend_usd.*improved_baseline",
        )
        _require_pattern(
            self,
            contracts,
            r"## Section 2.*Handoff Envelope.*recommended_next_machine_state.*state_patch.*directive",
        )
        _require_pattern(
            self,
            contracts,
            r"## Section 3.*Worker Result File",
        )
        _require_pattern(
            self,
            backend,
            r"## Forbidden Operations.*protocol breach",
        )

    def test_runtime_docs_define_reinvocation_and_worker_lanes(self) -> None:
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
        _require_pattern(self, skill, r"\.ml-metaopt/.*state\.json")
        _require_pattern(
            self,
            machine,
            r"SELECT_AND_DESIGN_SWEEP.*current_pool_frozen.*true",
        )
        _require_pattern(
            self,
            machine,
            r"IDEATE.*proposal.*proposal_policy\.current_target",
        )
        _require_pattern(
            self,
            machine,
            r"ROLL_ITERATION.*current_sweep.*null.*selected_sweep.*null",
        )
        _require_pattern(
            self,
            lanes,
            r"Ideation Lane.*metaopt-ideation-worker",
        )
        _require_pattern(
            self,
            lanes,
            r"Analysis Lane.*metaopt-analysis-worker",
        )
        _require_pattern(
            self,
            lanes,
            r"Execution Lane.*skypilot-wandb-worker",
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
        _require_pattern(self, dependencies, r"`git`.*PATH")
        _require_pattern(self, dependencies, r"SkyPilot")
        _require_pattern(self, dependencies, r"[Hh]ost reinvocation mechanism")
        _require_pattern(
            self,
            dependencies,
            r"`ml_metaopt_campaign\.yaml`.*`AGENTS\.md`.*`\.ml-metaopt/.*state\.json`.*created on first run if absent",
        )
        _require_pattern(
            self,
            dependencies,
            r"`SKILL\.md`.*`references/contracts\.md`.*`references/state-machine\.md`.*"
            r"`references/worker-lanes\.md`.*`references/dispatch-guide\.md`.*"
            r"`references/backend-contract\.md`.*"
            r"`references/control-protocol\.md`",
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
            "metaopt-analysis-worker",
            "skypilot-wandb-worker",
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

    def test_worker_lanes_has_execution_lane(self) -> None:
        """worker-lanes.md must document the execution lane."""
        worker_lanes = _read_text("references/worker-lanes.md")
        self.assertIn("## Execution Lane", worker_lanes)
        self.assertIn("skypilot-wandb-worker", worker_lanes)

    def test_contracts_documents_dispatch_types(self) -> None:
        """contracts.md must document directive types and worker result paths."""
        contracts = _read_text("references/contracts.md")
        self.assertIn("launch_sweep", contracts)
        self.assertIn("poll_sweep", contracts)
        self.assertIn("run_smoke_test", contracts)
        self.assertIn("worker-results", contracts)

    def test_skill_availability_section_exists(self) -> None:
        """SKILL.md must document worker targets and their roles."""
        skill_md = _read_text("SKILL.md")
        self.assertIn("## Worker Targets", skill_md)
        for skill_name in [
            "metaopt-ideation-worker",
            "metaopt-analysis-worker",
            "skypilot-wandb-worker",
        ]:
            self.assertIn(skill_name, skill_md,
                          f"{skill_name} not in SKILL.md Worker Targets")

    def test_delegation_list_includes_all_worker_skills(self) -> None:
        """SKILL.md worker policy must reference the ideation worker and analysis worker."""
        skill_md = _read_text("SKILL.md")
        worker_section = skill_md.split("## Worker Policy")[1].split("## Worker Targets")[0]
        self.assertIn("metaopt-ideation-worker", worker_section)
        self.assertIn("metaopt-analysis-worker", worker_section)

    def test_proposal_record_shape_documented(self) -> None:
        """contracts.md must define proposal record shape with required fields."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(self, contracts, r"proposal.*object")
        _require_pattern(self, contracts, r"proposal_id.*string.*[Nn]on-empty.*unique within the campaign")
        _require_pattern(self, contracts, r"rationale.*string")
        _require_pattern(self, contracts, r"sweep_config.*object")
        _require_pattern(self, contracts, r"WandB sweep config")

    def test_selected_sweep_expanded_shape(self) -> None:
        """selected_sweep must document proposal_id and sweep_config."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(self, contracts, r"selected_sweep.*proposal_id.*string")
        _require_pattern(self, contracts, r"selected_sweep.*sweep_config.*object")
        _require_pattern(self, contracts, r"WandB sweep config.*method.*metric.*parameters")
        # selected_sweep is nulled during ROLL_ITERATION
        machine = _read_text("references/state-machine.md")
        _require_pattern(self, machine, r"ROLL_ITERATION.*selected_sweep.*null")

    def test_dispatch_guide_has_worker_dispatch_details(self) -> None:
        """dispatch-guide.md must define per-state dispatch details including worker and model class."""
        guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, guide, r"## IDEATE")
        _require_pattern(self, guide, r"metaopt-ideation-worker")
        _require_pattern(self, guide, r"general_worker")
        _require_pattern(self, guide, r"strong_reasoner")
        _require_pattern(self, guide, r"## ANALYZE")
        _require_pattern(self, guide, r"metaopt-analysis-worker")

    def test_local_sanity_fail_fast_documented(self) -> None:
        """state-machine.md must document LOCAL_SANITY fail-fast with no remediation."""
        sm = _read_text("references/state-machine.md")
        _require_pattern(self, sm, r"LOCAL_SANITY.*60-second.*hard.*timeout")
        _require_pattern(self, sm, r"LOCAL_SANITY.*FAILED.*exit_code.*timed_out")

    def test_remote_failure_handling_path(self) -> None:
        """WAIT_FOR_SWEEP failures must route to FAILED or BLOCKED_CONFIG."""
        sm = _read_text("references/state-machine.md")
        _require_pattern(self, sm, r"WAIT_FOR_SWEEP.*FAILED.*crashed")
        guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, guide, r"WAIT_FOR_SWEEP.*sweep_status.*failed")

    def test_execution_directives_documented(self) -> None:
        """dispatch-guide.md must document all three execution directive types."""
        guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, guide, r"launch_sweep.*skypilot-wandb-worker")
        _require_pattern(self, guide, r"poll_sweep.*skypilot-wandb-worker")
        _require_pattern(self, guide, r"run_smoke_test.*skypilot-wandb-worker")

    def test_local_sanity_action_routing_complete(self) -> None:
        """dispatch-guide.md LOCAL_SANITY section must document pass/fail routing."""
        guide = _read_text("references/dispatch-guide.md")
        local_sanity_section = guide.split("## LOCAL_SANITY")[1].split("## LAUNCH_SWEEP")[0]
        self.assertIn("run_smoke_test", local_sanity_section)
        self.assertIn("LAUNCH_SWEEP", local_sanity_section)
        self.assertIn("FAILED", local_sanity_section)

    def test_v4_state_fields_in_state_schema(self) -> None:
        """contracts.md state file must include v4 fields: current_sweep, selected_sweep, key_learnings."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(self, contracts, r"current_sweep")
        _require_pattern(self, contracts, r"selected_sweep")
        _require_pattern(self, contracts, r"key_learnings.*list")
        _require_pattern(self, contracts, r"completed_iterations.*list")
        _require_pattern(self, contracts, r"no_improve_iterations.*integer")

    def test_no_code_patches_in_v4(self) -> None:
        """v4 does not produce code patches; worker-lanes.md must document drift rules forbidding them."""
        lanes = _read_text("references/worker-lanes.md")
        _require_pattern(self, lanes, r"MUST NOT.*code patches|MUST NOT.*file diffs")
        skill_md = _read_text("SKILL.md")
        _require_pattern(self, skill_md, r"does NOT produce code patches")

    def test_dispatch_guide_ideation_step(self) -> None:
        """dispatch-guide.md IDEATE section must document worker dispatch for proposals."""
        guide = _read_text("references/dispatch-guide.md")
        ideation_section = guide.split("## IDEATE")[1].split("## WAIT_FOR_PROPOSALS")[0]
        self.assertIn("metaopt-ideation-worker", ideation_section)
        self.assertIn("background", ideation_section)
        self.assertIn("proposal", ideation_section)

    # --- Control Protocol tests ---

    def test_control_protocol_reference_exists(self) -> None:
        """references/control-protocol.md must exist and be non-trivial."""
        protocol = _read_text("references/control-protocol.md")
        self.assertGreater(len(protocol), 200, "control-protocol.md is too short to be meaningful")

    def test_control_protocol_defines_handoff_envelope(self) -> None:
        """control-protocol.md must define the universal control-handoff envelope with all required fields."""
        protocol = _read_text("references/control-protocol.md")
        required_fields = [
            "recommended_next_machine_state",
            "state_patch",
            "directive",
        ]
        for field in required_fields:
            self.assertIn(field, protocol, f"control-protocol.md must define envelope field '{field}'")

    def test_control_protocol_lists_all_control_agents(self) -> None:
        """state-machine.md Control Agent Dispatch Table must reference all six control agents."""
        state_machine = _read_text("references/state-machine.md")
        control_agents = [
            "metaopt-load-campaign",
            "metaopt-hydrate-state",
            "metaopt-background-control",
            "metaopt-select-design",
            "metaopt-remote-execution-control",
            "metaopt-iteration-close-control",
        ]
        for agent in control_agents:
            self.assertIn(agent, state_machine, f"state-machine.md must reference control agent '{agent}'")

    def test_control_protocol_defines_state_patch_ownership(self) -> None:
        """control-protocol.md must define state-patch ownership rules."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"[Oo]wnership|STATE_PATCH_OWNERSHIP")
        _require_pattern(self, protocol, r"state_patch.*keys.*authorized|unauthorized.*BLOCKED_PROTOCOL")

    def test_skill_md_references_control_protocol(self) -> None:
        """SKILL.md Required References must include references/control-protocol.md."""
        skill_md = _read_text("SKILL.md")
        self.assertIn("references/control-protocol.md", skill_md)

    def test_skill_md_describes_orchestrator_as_transport(self) -> None:
        """SKILL.md must describe the orchestrator as delegating semantic decisions to control agents."""
        skill_md = _read_text("SKILL.md")
        _require_pattern(self, skill_md, r"delegate.*semantic|semantic.*delegate")
        _require_pattern(self, skill_md, r"[Cc]ontrol [Aa]gent")

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
            "launch_sweep",
            "poll_sweep",
            "run_smoke_test",
            "remove_agents_hook",
            "delete_state_file",
            "emit_final_report",
            "emit_iteration_report",
            "none",
        ):
            self.assertIn(action_name, protocol)

    def test_control_protocol_documents_local_directive_fields(self) -> None:
        """control-protocol.md must describe directive execution steps."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"launch_sweep.*skypilot-wandb-worker")
        _require_pattern(self, protocol, r"poll_sweep.*skypilot-wandb-worker")
        _require_pattern(self, protocol, r"run_smoke_test.*skypilot-wandb-worker")
        _require_pattern(self, protocol, r"remove_agents_hook.*AGENTS\.md")

    def test_directive_docs_require_mechanical_execution_not_inference(self) -> None:
        """Directive docs must say the orchestrator executes directives mechanically instead of inferring executor work from prose."""
        protocol = _read_text("references/control-protocol.md")
        dispatch_guide = _read_text("references/dispatch-guide.md")
        _require_pattern(self, protocol, r"execut.*mechanically")
        _require_pattern(self, protocol, r"never.*semantic|never performs semantic")
        _require_pattern(self, dispatch_guide, r"directive")

    def test_contracts_document_campaign_started_at(self) -> None:
        """contracts.md must list campaign_started_at as a state-file field."""
        contracts = _read_text("references/contracts.md")
        self.assertIn("campaign_started_at", contracts)
        # Must appear in the state file schema section
        state_section = contracts.split("## Section 1")[1].split("## Section 2")[0]
        self.assertIn("campaign_started_at", state_section,
                        "campaign_started_at must be in the State File Schema section")

    def test_state_machine_documents_stop_conditions_in_roll_iteration(self) -> None:
        """state-machine.md ROLL_ITERATION must document stop conditions."""
        state_machine = _read_text("references/state-machine.md")
        self.assertIn("max_iterations", state_machine)
        self.assertIn("max_no_improve_iterations", state_machine)
        self.assertIn("target_metric", state_machine)

    def test_state_machine_documents_terminal_cleanup_directives(self) -> None:
        """state-machine.md terminal states must document explicit cleanup directives."""
        state_machine = _read_text("references/state-machine.md")
        self.assertIn("remove_agents_hook", state_machine)
        self.assertIn("emit_final_report", state_machine)

    def test_control_agent_manifests_reference_control_protocol(self) -> None:
        """Every control-agent manifest must reference the handoff pattern from control-protocol.md."""
        control_agent_manifests = [
            ".github/agents/metaopt-load-campaign.agent.md",
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            self.assertIn(
                "handoff",
                content.lower(),
                f"{manifest_path} must reference the handoff pattern",
            )

    def test_control_agent_manifests_state_handoff_conformance(self) -> None:
        """Every control-agent manifest must output a handoff file with the standard envelope."""
        control_agent_manifests = [
            ".github/agents/metaopt-load-campaign.agent.md",
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            self.assertIn(
                "recommended_next_machine_state",
                content,
                f"{manifest_path} must output recommended_next_machine_state",
            )
            self.assertIn(
                "state_patch",
                content,
                f"{manifest_path} must output state_patch",
            )

    def test_control_agent_manifests_declare_directives_authoritative(self) -> None:
        """Every control-agent manifest must declare directive as the executor input."""
        control_agent_manifests = [
            ".github/agents/metaopt-load-campaign.agent.md",
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            self.assertIn(
                "directive",
                content,
                f"{manifest_path} must mention directive",
            )

    def test_control_agent_manifests_do_not_apply_state_in_agent_commands(self) -> None:
        """Control agents must emit handoffs only; state application is orchestrator-owned."""
        control_agent_manifests = [
            ".github/agents/metaopt-hydrate-state.agent.md",
            ".github/agents/metaopt-background-control.agent.md",
            ".github/agents/metaopt-select-design.agent.md",
            ".github/agents/metaopt-remote-execution-control.agent.md",
            ".github/agents/metaopt-iteration-close-control.agent.md",
        ]
        for manifest_path in control_agent_manifests:
            content = _read_text(manifest_path)
            # v4 agents must not write state directly
            _require_pattern(
                self,
                content,
                r"[Dd]o NOT write.*state\.json|NOT.*write.*state\.json.*directly",
            )

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
        _require_pattern(self, sm, r"## State List.*BLOCKED_PROTOCOL")
        _require_pattern(self, sm, r"BLOCKED_PROTOCOL.*terminal")

    def test_blocked_protocol_in_skill_md_diagram(self) -> None:
        """SKILL.md state-machine diagram must include BLOCKED_PROTOCOL as a
        terminal node."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r'"BLOCKED_PROTOCOL".*doublecircle')
        # In v4, BLOCKED_PROTOCOL is declared but implicit transitions aren't all shown in diagram
        _require_pattern(self, skill, r'BLOCKED_PROTOCOL')

    def test_blocked_protocol_transition_edges_in_state_machine(self) -> None:
        """state-machine.md must document BLOCKED_PROTOCOL transition from HYDRATE_STATE."""
        sm = _read_text("references/state-machine.md")
        _require_pattern(self, sm, r"HYDRATE_STATE.*BLOCKED_PROTOCOL")

    def test_blocked_protocol_validation_failure_in_control_protocol(self) -> None:
        """control-protocol.md must document that handoff validation failures
        transition to BLOCKED_PROTOCOL."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"BLOCKED_PROTOCOL")
        _require_pattern(self, protocol, r"validation failure.*BLOCKED_PROTOCOL|BLOCKED_PROTOCOL.*validation")

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
        """control-protocol.md must document BLOCKED_PROTOCOL transitions."""
        protocol = _read_text("references/control-protocol.md")
        self.assertIn("BLOCKED_PROTOCOL", protocol)
        _require_pattern(self, protocol, r"BLOCKED_PROTOCOL")

    def test_semantic_fallback_forbidden_in_skill_md(self) -> None:
        """SKILL.md must explicitly forbid the orchestrator from making semantic decisions."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r"[Mm]ust delegate all semantic decisions|[Nn]ever.*semantic")

    def test_preferred_model_documented_in_dispatch_guide(self) -> None:
        """dispatch-guide.md or worker-lanes.md must document model classes."""
        lanes = _read_text("references/worker-lanes.md")
        self.assertIn("strong_reasoner", lanes)
        self.assertIn("claude-opus-4.6", lanes)
        self.assertIn("general_worker", lanes)

    def test_preferred_model_documented_in_control_protocol(self) -> None:
        """SKILL.md must document model resolution classes."""
        skill = _read_text("SKILL.md")
        self.assertIn("strong_reasoner", skill)
        self.assertIn("general_worker", skill)

    def test_strong_coder_not_in_guardrail_preferred_model_map(self) -> None:
        """v4 removed strong_coder — no code-writing workers are dispatched."""
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from _guardrail_utils import PREFERRED_MODEL_BY_CLASS
        self.assertNotIn("strong_coder", PREFERRED_MODEL_BY_CLASS)

    def test_worker_artifact_preconditions_in_worker_lanes(self) -> None:
        """worker-lanes.md must document lane drift rules for all worker types."""
        lanes = _read_text("references/worker-lanes.md")
        _require_pattern(self, lanes, r"Lane drift rules.*MUST NOT")
        _require_pattern(self, lanes, r"Analysis Lane.*metaopt-analysis-worker")
        _require_pattern(self, lanes, r"Ideation Lane.*metaopt-ideation-worker")

    def test_queue_only_backend_contract_strengthened(self) -> None:
        """backend-contract.md must explicitly prohibit raw SSH and
        direct API bypass."""
        backend = _read_text("references/backend-contract.md")
        _require_pattern(self, backend, r"[Rr]aw SSH")
        _require_pattern(self, backend, r"[Pp]rotocol breach")

    def test_backend_contract_blocks_direct_fallback_tools_by_name(self) -> None:
        """backend-contract.md should explicitly name the ad-hoc tools that
        must never be used as remote-execution fallbacks."""
        backend = _read_text("references/backend-contract.md")
        _require_pattern(self, backend, r"ray job submit|[Rr]ay CLI")
        _require_pattern(self, backend, r"sky exec")
        _require_pattern(self, backend, r"[Dd]irect Vast\.ai API")

    def test_control_protocol_forbids_hand_authored_semantic_state_edits(self) -> None:
        """control-protocol.md must say the orchestrator never performs
        semantic decisions and only applies control-agent state patches."""
        protocol = _read_text("references/control-protocol.md")
        _require_pattern(self, protocol, r"never.*semantic|never performs semantic")
        _require_pattern(self, protocol, r"state_patch")
        _require_pattern(self, protocol, r"recommended_next_machine_state")

    def test_skill_md_forbids_direct_project_file_edits_by_orchestrator(self) -> None:
        """SKILL.md must make clear this skill does not produce code patches."""
        skill = _read_text("SKILL.md")
        _require_pattern(self, skill, r"does NOT produce code patches")
        _require_pattern(self, skill, r"does not dispatch code-writing workers|No.*strong_coder")

    def test_remote_control_manifest_forbids_raw_remote_fallbacks(self) -> None:
        """The remote control-agent manifest should explicitly forbid
        running remote commands directly."""
        manifest = _read_text(".github/agents/metaopt-remote-execution-control.agent.md")
        _require_pattern(self, manifest, r"[Dd]o NOT.*run remote commands|[Nn]ever.*remote")
        _require_pattern(self, manifest, r"SSH|SkyPilot CLI|WandB CLI")

    def test_iteration_close_manifest_forbids_manual_state_closure(self) -> None:
        """The iteration-close control-agent manifest should explicitly forbid
        direct state file writes."""
        manifest = _read_text(".github/agents/metaopt-iteration-close-control.agent.md")
        _require_pattern(self, manifest, r"[Dd]o NOT write.*state\.json|NOT.*write.*state\.json.*directly")
        _require_pattern(self, manifest, r"COMPLETE|BLOCKED_CONFIG")

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

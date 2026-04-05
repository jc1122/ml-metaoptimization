from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
VALID_MACHINE_STATES = {
    "LOAD_CAMPAIGN",
    "HYDRATE_STATE",
    "MAINTAIN_BACKGROUND_POOL",
    "WAIT_FOR_PROPOSAL_THRESHOLD",
    "SELECT_EXPERIMENT",
    "DESIGN_EXPERIMENT",
    "MATERIALIZE_CHANGESET",
    "LOCAL_SANITY",
    "ENQUEUE_REMOTE_BATCH",
    "WAIT_FOR_REMOTE_BATCH",
    "ANALYZE_RESULTS",
    "ROLL_ITERATION",
    "QUIESCE_SLOTS",
    "COMPLETE",
    "BLOCKED_CONFIG",
    "FAILED",
}
TERMINAL_MACHINE_STATES = {"COMPLETE", "BLOCKED_CONFIG", "FAILED"}
VALID_STATE_STATUSES = {"RUNNING", "BLOCKED_CONFIG", "FAILED", "COMPLETE"}
VALID_SLOT_CLASSES = {"background", "auxiliary"}
VALID_SLOT_MODES = {
    "ideation",
    "maintenance",
    "synthesis",
    "design",
    "materialization",
    "diagnosis",
    "analysis",
}
VALID_MODEL_CLASSES = {"strong_coder", "strong_reasoner", "general_worker"}
VALID_REMOTE_BATCH_STATUSES = {"queued", "running", "completed", "failed"}


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


def _validate_backend_payload(kind: str, payload: dict) -> None:
    assert isinstance(payload, dict), "backend payload must be an object"
    assert isinstance(payload.get("batch_id"), str) and payload["batch_id"], "batch_id is required"

    if kind == "enqueue":
        assert payload.get("status") == "queued", "enqueue payload must report queued status"
        assert isinstance(payload.get("queue_ref"), str) and payload["queue_ref"], "queue_ref is required"
        return

    if kind == "status":
        assert payload.get("status") in VALID_REMOTE_BATCH_STATUSES, "invalid batch lifecycle status"
        timestamps = payload.get("timestamps")
        assert isinstance(timestamps, dict) and timestamps, "timestamps map is required"
        return

    if kind == "results":
        assert payload.get("status") == "completed", "results payload must be completed"
        aggregate = payload.get("best_aggregate_result")
        assert isinstance(aggregate, dict), "best_aggregate_result is required"
        assert isinstance(aggregate.get("metric"), str) and aggregate["metric"], "aggregate metric is required"
        assert isinstance(aggregate.get("value"), (int, float)), "aggregate value is required"
        per_dataset = payload.get("per_dataset")
        assert isinstance(per_dataset, dict) and per_dataset, "per_dataset results are required"
        artifact_locations = payload.get("artifact_locations")
        assert isinstance(artifact_locations, dict) and artifact_locations, "artifact_locations are required"
        assert isinstance(artifact_locations.get("code"), str) and artifact_locations["code"], "artifact_locations.code is required"
        assert isinstance(artifact_locations.get("data_manifest"), str) and artifact_locations["data_manifest"], "artifact_locations.data_manifest is required"
        assert isinstance(payload.get("logs_location"), str) and payload["logs_location"], "logs_location is required"
        return

    raise AssertionError(f"unsupported payload kind: {kind}")


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
    assert payload["status"] in VALID_STATE_STATUSES, "invalid coarse status"
    assert payload["machine_state"] in VALID_MACHINE_STATES, "invalid machine state"

    assert "proposal_cycle" in payload, "proposal_cycle is required"
    proposal_cycle = payload["proposal_cycle"]
    assert isinstance(proposal_cycle, dict), "proposal_cycle must be an object"
    assert isinstance(proposal_cycle.get("cycle_id"), str) and proposal_cycle["cycle_id"], "proposal_cycle.cycle_id is required"
    assert isinstance(proposal_cycle.get("current_pool_frozen"), bool), "proposal_cycle.current_pool_frozen is required"
    assert isinstance(proposal_cycle.get("ideation_rounds_by_slot"), dict), "proposal_cycle.ideation_rounds_by_slot is required"
    assert "shortfall_reason" in proposal_cycle, "proposal_cycle.shortfall_reason is required"

    if payload["status"] == "RUNNING":
        assert payload["machine_state"] not in TERMINAL_MACHINE_STATES, "running state cannot point at a terminal machine state"
    else:
        assert payload["machine_state"] == payload["status"], "terminal status must mirror machine_state"
        assert payload["active_slots"] == [], "terminal states must have no active slots"

    assert isinstance(payload["active_slots"], list), "active_slots must be a list"
    for slot in payload["active_slots"]:
        assert isinstance(slot, dict), "active_slots entries must be objects"
        assert slot.get("slot_class") in VALID_SLOT_CLASSES, "invalid slot_class"
        assert slot.get("mode") in VALID_SLOT_MODES, "invalid slot mode"
        assert slot.get("model_class") in VALID_MODEL_CLASSES, "invalid model_class"
        assert isinstance(slot.get("requested_model"), str) and slot["requested_model"], "requested_model is required"
        assert isinstance(slot.get("resolved_model"), str) and slot["resolved_model"], "resolved_model is required"
        assert isinstance(slot.get("task_summary"), str) and slot["task_summary"], "slot must have task_summary"

    local_changeset = payload["local_changeset"]
    assert isinstance(local_changeset, dict), "local_changeset must be an object"
    assert isinstance(local_changeset.get("integration_worktree"), str) and local_changeset["integration_worktree"], "integration_worktree is required"
    assert isinstance(local_changeset.get("patch_artifacts"), list), "patch_artifacts list is required"
    assert isinstance(local_changeset.get("apply_results"), list), "apply_results list is required"
    assert isinstance(local_changeset.get("verification_notes"), list), "verification_notes list is required"
    assert isinstance(local_changeset.get("code_artifact_uri"), str) and local_changeset["code_artifact_uri"], "code_artifact_uri is required"
    assert isinstance(local_changeset.get("data_manifest_uri"), str) and local_changeset["data_manifest_uri"], "data_manifest_uri is required"


class MetaoptValidationTests(unittest.TestCase):
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
            r"## Batch Manifest Contract.*`artifacts\.code_artifact\.uri`.*`artifacts\.data_manifest\.uri`",
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

        _require_pattern(self, skill, r"continuous across reinvocations")
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

    def test_backend_and_state_fixtures_validate(self) -> None:
        _validate_backend_payload("enqueue", _read_json("tests/fixtures/backend/enqueue-valid.json"))
        _validate_backend_payload("status", _read_json("tests/fixtures/backend/status-valid.json"))
        _validate_backend_payload("results", _read_json("tests/fixtures/backend/results-valid.json"))
        _validate_state_payload(_read_json("tests/fixtures/state/running.json"))
        _validate_state_payload(_read_json("tests/fixtures/state/complete.json"))

    def test_v3_state_fixtures_require_resume_and_changeset_metadata(self) -> None:
        running = _read_json("tests/fixtures/state/running.json")
        complete = _read_json("tests/fixtures/state/complete.json")

        for fixture in (running, complete):
            self.assertEqual(fixture["version"], 3)
            self.assertIn("proposal_cycle", fixture)
            self.assertIn("integration_worktree", fixture["local_changeset"])
            self.assertIn("data_manifest_uri", fixture["local_changeset"])

        for slot in running["active_slots"]:
            self.assertIn("model_class", slot)
            self.assertIn("requested_model", slot)
            self.assertIn("resolved_model", slot)

    def test_invalid_fixtures_are_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            _validate_backend_payload("enqueue", _read_json("tests/fixtures/backend/enqueue-invalid-missing-batch-id.json"))

        with self.assertRaises(AssertionError):
            _validate_backend_payload("status", _read_json("tests/fixtures/backend/status-invalid-lifecycle.json"))

        with self.assertRaises(AssertionError):
            _validate_state_payload(_read_json("tests/fixtures/state/invalid-status-pair.json"))

        with self.assertRaisesRegex(AssertionError, r"proposal_cycle is required"):
            _validate_state_payload(_read_json("tests/fixtures/state/invalid-missing-proposal-cycle.json"))

        with self.assertRaisesRegex(AssertionError, r"resolved_model is required"):
            _validate_state_payload(_read_json("tests/fixtures/state/invalid-missing-slot-model-resolution.json"))

        with self.assertRaisesRegex(AssertionError, r"data_manifest_uri is required"):
            _validate_state_payload(_read_json("tests/fixtures/state/invalid-missing-local-changeset-metadata.json"))

    def test_state_validator_rejects_malformed_nested_sections_with_clear_messages(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        malformed_proposal_cycle = dict(fixture)
        malformed_proposal_cycle["proposal_cycle"] = []
        with self.assertRaisesRegex(AssertionError, r"proposal_cycle must be an object"):
            _validate_state_payload(malformed_proposal_cycle)

        malformed_active_slots = dict(fixture)
        malformed_active_slots["active_slots"] = [[]]
        with self.assertRaisesRegex(AssertionError, r"active_slots entries must be objects"):
            _validate_state_payload(malformed_active_slots)

        malformed_local_changeset = dict(fixture)
        malformed_local_changeset["local_changeset"] = []
        with self.assertRaisesRegex(AssertionError, r"local_changeset must be an object"):
            _validate_state_payload(malformed_local_changeset)


if __name__ == "__main__":
    unittest.main()

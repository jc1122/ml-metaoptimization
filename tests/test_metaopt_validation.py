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
    assert payload["version"] == 2, "state fixture must use v2"
    assert payload["status"] in VALID_STATE_STATUSES, "invalid coarse status"
    assert payload["machine_state"] in VALID_MACHINE_STATES, "invalid machine state"

    if payload["status"] == "RUNNING":
        assert payload["machine_state"] not in TERMINAL_MACHINE_STATES, "running state cannot point at a terminal machine state"
    else:
        assert payload["machine_state"] == payload["status"], "terminal status must mirror machine_state"
        assert payload["active_slots"] == [], "terminal states must have no active slots"

    for slot in payload["active_slots"]:
        assert slot["slot_class"] in VALID_SLOT_CLASSES, "invalid slot_class"
        assert slot["mode"] in VALID_SLOT_MODES, "invalid slot mode"
        assert isinstance(slot["task_summary"], str) and slot["task_summary"], "slot must have task_summary"


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

    def test_invalid_fixtures_are_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            _validate_backend_payload("enqueue", _read_json("tests/fixtures/backend/enqueue-invalid-missing-batch-id.json"))

        with self.assertRaises(AssertionError):
            _validate_backend_payload("status", _read_json("tests/fixtures/backend/status-invalid-lifecycle.json"))

        with self.assertRaises(AssertionError):
            _validate_state_payload(_read_json("tests/fixtures/state/invalid-status-pair.json"))


if __name__ == "__main__":
    unittest.main()

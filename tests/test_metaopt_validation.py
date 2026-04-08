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
    "BLOCKED_PROTOCOL",
    "FAILED",
}
TERMINAL_MACHINE_STATES = {"COMPLETE", "BLOCKED_CONFIG", "BLOCKED_PROTOCOL", "FAILED"}
VALID_STATE_STATUSES = {"RUNNING", "BLOCKED_CONFIG", "BLOCKED_PROTOCOL", "FAILED", "COMPLETE"}
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
PRE_SELECTION_MACHINE_STATES = {
    "LOAD_CAMPAIGN",
    "HYDRATE_STATE",
    "MAINTAIN_BACKGROUND_POOL",
    "WAIT_FOR_PROPOSAL_THRESHOLD",
    "SELECT_EXPERIMENT",
}
POST_SELECTION_CLEARED_MACHINE_STATES = {"ROLL_ITERATION", "QUIESCE_SLOTS", "COMPLETE"}
PRE_MATERIALIZATION_MACHINE_STATES = PRE_SELECTION_MACHINE_STATES | {
    "DESIGN_EXPERIMENT",
    "MATERIALIZE_CHANGESET",
}
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LEGACY_MAX_BATCH_RETRIES_KEY = "max_batch_" "retries"


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
        _require_number(aggregate.get("value"), "aggregate value must be numeric")
        per_dataset = payload.get("per_dataset")
        assert isinstance(per_dataset, dict) and per_dataset, "per_dataset results are required"
        artifact_locations = payload.get("artifact_locations")
        assert isinstance(artifact_locations, dict) and artifact_locations, "artifact_locations are required"
        assert isinstance(artifact_locations.get("code"), str) and artifact_locations["code"], "artifact_locations.code is required"
        assert isinstance(artifact_locations.get("data_manifest"), str) and artifact_locations["data_manifest"], "artifact_locations.data_manifest is required"
        assert isinstance(payload.get("logs_location"), str) and payload["logs_location"], "logs_location is required"
        return

    raise AssertionError(f"unsupported payload kind: {kind}")


def _validate_batch_manifest(payload: dict) -> None:
    assert isinstance(payload, dict), "batch manifest must be an object"
    assert payload.get("version") == 3, "batch manifest must use v3"
    _require_non_empty_string(payload.get("campaign_id"), "campaign_id is required")
    assert type(payload.get("iteration")) is int and payload["iteration"] > 0, "iteration is required"
    _require_non_empty_string(payload.get("batch_id"), "batch_id is required")
    experiment = payload.get("experiment")
    assert isinstance(experiment, dict) and experiment, "experiment is required"
    _require_non_empty_string(experiment.get("proposal_id"), "experiment.proposal_id is required")

    retry_policy = payload.get("retry_policy")
    assert isinstance(retry_policy, dict) and retry_policy, "retry_policy is required"
    assert type(retry_policy.get("max_attempts")) is int and retry_policy["max_attempts"] > 0, (
        "retry_policy.max_attempts is required"
    )

    artifacts = payload.get("artifacts")
    assert isinstance(artifacts, dict), "artifacts is required"
    code_artifact = artifacts.get("code_artifact")
    assert isinstance(code_artifact, dict), "artifacts.code_artifact is required"
    _require_non_empty_string(code_artifact.get("uri"), "artifacts.code_artifact.uri is required")

    data_manifest = artifacts.get("data_manifest")
    assert isinstance(data_manifest, dict), "artifacts.data_manifest is required"
    _require_non_empty_string(data_manifest.get("uri"), "artifacts.data_manifest.uri is required")

    execution = payload.get("execution")
    assert isinstance(execution, dict), "execution is required"
    _require_non_empty_string(execution.get("entrypoint"), "execution.entrypoint is required")


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
        "campaign_started_at",
    }
    missing = required_keys - payload.keys()
    assert not missing, f"missing required state keys: {sorted(missing)}"
    assert payload["version"] == 3, "state fixture must use v3"
    _require_non_empty_string(payload["campaign_id"], "campaign_id is required")
    _require_sha256_digest(payload["campaign_identity_hash"], "campaign_identity_hash must be a sha256 digest")
    _require_sha256_digest(payload["runtime_config_hash"], "runtime_config_hash must be a sha256 digest")
    assert payload["status"] in VALID_STATE_STATUSES, "invalid coarse status"
    assert payload["machine_state"] in VALID_MACHINE_STATES, "invalid machine state"
    assert type(payload["current_iteration"]) is int and payload["current_iteration"] > 0, (
        "current_iteration must be a positive integer"
    )
    _require_non_empty_string(payload["next_action"], "next_action is required")

    objective_snapshot = payload["objective_snapshot"]
    assert isinstance(objective_snapshot, dict), "objective_snapshot must be an object"
    _require_non_empty_string(objective_snapshot.get("metric"), "objective_snapshot.metric is required")
    assert objective_snapshot.get("direction") in {"minimize", "maximize"}, (
        "objective_snapshot.direction must be minimize or maximize"
    )
    assert isinstance(objective_snapshot.get("aggregation"), dict), "objective_snapshot.aggregation must be an object"
    _require_number(
        objective_snapshot.get("improvement_threshold"),
        "objective_snapshot.improvement_threshold must be numeric",
    )

    assert "proposal_cycle" in payload, "proposal_cycle is required"
    proposal_cycle = payload["proposal_cycle"]
    assert isinstance(proposal_cycle, dict), "proposal_cycle must be an object"
    assert isinstance(proposal_cycle.get("cycle_id"), str) and proposal_cycle["cycle_id"], "proposal_cycle.cycle_id is required"
    assert isinstance(proposal_cycle.get("current_pool_frozen"), bool), "proposal_cycle.current_pool_frozen is required"
    ideation_rounds_by_slot = proposal_cycle.get("ideation_rounds_by_slot")
    assert isinstance(ideation_rounds_by_slot, dict), "proposal_cycle.ideation_rounds_by_slot is required"
    for slot_id, rounds in ideation_rounds_by_slot.items():
        _require_non_empty_string(slot_id, "proposal_cycle.ideation_rounds_by_slot keys must be non-empty strings")
        assert type(rounds) is int and rounds >= 0, (
            "proposal_cycle.ideation_rounds_by_slot values must be non-negative integers"
        )
    assert "shortfall_reason" in proposal_cycle, "proposal_cycle.shortfall_reason is required"

    if payload["status"] == "RUNNING":
        assert payload["machine_state"] not in TERMINAL_MACHINE_STATES, "running state cannot point at a terminal machine state"
    else:
        assert payload["machine_state"] == payload["status"], "terminal status must mirror machine_state"
        assert payload["active_slots"] == [], "terminal states must have no active slots"

    assert isinstance(payload["active_slots"], list), "active_slots must be a list"
    for slot in payload["active_slots"]:
        assert isinstance(slot, dict), "active_slots entries must be objects"
        _require_non_empty_string(slot.get("slot_id"), "slot_id is required")
        assert slot.get("slot_class") in VALID_SLOT_CLASSES, "invalid slot_class"
        assert slot.get("mode") in VALID_SLOT_MODES, "invalid slot mode"
        assert slot.get("model_class") in VALID_MODEL_CLASSES, "invalid model_class"
        if slot["mode"] == "materialization":
            assert slot["model_class"] == "strong_coder", (
                "materialization slots must use model_class strong_coder"
            )
        assert isinstance(slot.get("requested_model"), str) and slot["requested_model"], "requested_model is required"
        assert isinstance(slot.get("resolved_model"), str) and slot["resolved_model"], "resolved_model is required"
        _require_non_empty_string(slot.get("status"), "slot status is required")
        assert type(slot.get("attempt")) is int and slot["attempt"] > 0, "slot attempt must be a positive integer"
        assert isinstance(slot.get("task_summary"), str) and slot["task_summary"], "slot must have task_summary"

    assert isinstance(payload["current_proposals"], list), "current_proposals must be a list"
    for proposal in payload["current_proposals"]:
        assert isinstance(proposal, dict), "current_proposals entries must be objects"

    assert isinstance(payload["next_proposals"], list), "next_proposals must be a list"
    for proposal in payload["next_proposals"]:
        assert isinstance(proposal, dict), "next_proposals entries must be objects"

    selected_experiment = payload["selected_experiment"]
    if selected_experiment is None:
        assert payload["machine_state"] in PRE_SELECTION_MACHINE_STATES | POST_SELECTION_CLEARED_MACHINE_STATES | {"BLOCKED_CONFIG", "BLOCKED_PROTOCOL"}, (
            "selected_experiment must be populated once SELECT_EXPERIMENT completes"
        )
    else:
        assert isinstance(selected_experiment, dict), "selected_experiment must be an object"
        _require_non_empty_string(
            selected_experiment.get("proposal_id"),
            "selected_experiment.proposal_id is required",
        )
        assert type(selected_experiment.get("sanity_attempts")) is int and selected_experiment["sanity_attempts"] >= 0, (
            "selected_experiment.sanity_attempts must be a non-negative integer"
        )

    baseline = payload["baseline"]
    assert isinstance(baseline, dict), "baseline must be an object"
    _require_number(baseline.get("aggregate"), "baseline.aggregate must be numeric")
    by_dataset = baseline.get("by_dataset")
    assert isinstance(by_dataset, dict) and by_dataset, "baseline.by_dataset must be a non-empty object"
    for dataset_id, aggregate in by_dataset.items():
        _require_non_empty_string(dataset_id, "baseline.by_dataset keys must be non-empty strings")
        _require_number(aggregate, "baseline.by_dataset values must be numeric")

    local_changeset = payload["local_changeset"]
    if local_changeset is None:
        assert payload["machine_state"] in PRE_MATERIALIZATION_MACHINE_STATES | {"BLOCKED_CONFIG", "BLOCKED_PROTOCOL"}, (
            "local_changeset must be populated once MATERIALIZE_CHANGESET completes"
        )
    else:
        assert isinstance(local_changeset, dict), "local_changeset must be an object"
        assert isinstance(local_changeset.get("integration_worktree"), str) and local_changeset["integration_worktree"], "integration_worktree is required"
        assert isinstance(local_changeset.get("patch_artifacts"), list), "patch_artifacts list is required"
        assert isinstance(local_changeset.get("apply_results"), list), "apply_results list is required"
        assert isinstance(local_changeset.get("verification_notes"), list), "verification_notes list is required"
        assert isinstance(local_changeset.get("code_artifact_uri"), str) and local_changeset["code_artifact_uri"], "code_artifact_uri is required"
        assert isinstance(local_changeset.get("data_manifest_uri"), str) and local_changeset["data_manifest_uri"], "data_manifest_uri is required"
        for patch_artifact in local_changeset["patch_artifacts"]:
            assert isinstance(patch_artifact, dict), "patch_artifacts entries must be objects"
            _require_non_empty_string(
                patch_artifact.get("producer_slot_id"),
                "patch_artifacts entries must include non-empty producer_slot_id",
            )
            _require_non_empty_string(
                patch_artifact.get("purpose"),
                "patch_artifacts entries must include non-empty purpose",
            )
            _require_non_empty_string(
                patch_artifact.get("patch_path"),
                "patch_artifacts entries must include non-empty patch_path",
            )
            _require_non_empty_string(
                patch_artifact.get("target_worktree"),
                "patch_artifacts entries must include non-empty target_worktree",
            )
        for apply_result in local_changeset["apply_results"]:
            assert isinstance(apply_result, dict), "apply_results entries must be objects"
            _require_non_empty_string(
                apply_result.get("patch_path"),
                "apply_results entries must include non-empty patch_path",
            )
            _require_non_empty_string(
                apply_result.get("status"),
                "apply_results entries must include non-empty status",
            )

    assert isinstance(payload["remote_batches"], list), "remote_batches must be a list"
    for remote_batch in payload["remote_batches"]:
        assert isinstance(remote_batch, dict), "remote_batches entries must be objects"
        _require_non_empty_string(
            remote_batch.get("batch_id"),
            "remote_batches entries must include non-empty batch_id",
        )
        _require_non_empty_string(
            remote_batch.get("queue_ref"),
            "remote_batches entries must include non-empty queue_ref",
        )
        assert remote_batch.get("status") in VALID_REMOTE_BATCH_STATUSES, (
            "remote_batches entries must include valid status"
        )

    completed_experiments = payload["completed_experiments"]
    assert isinstance(completed_experiments, list), "completed_experiments must be a list"
    for completed_experiment in completed_experiments:
        assert isinstance(completed_experiment, dict), "completed_experiments entries must be objects"
        _require_non_empty_string(
            completed_experiment.get("batch_id"),
            "completed_experiments entries must include non-empty batch_id",
        )
        _require_number(
            completed_experiment.get("aggregate"),
            "completed_experiments aggregate must be numeric",
        )

    key_learnings = payload["key_learnings"]
    assert isinstance(key_learnings, list), "key_learnings must be a list"
    for learning in key_learnings:
        _require_non_empty_string(learning, "key_learnings entries must be non-empty strings")

    assert type(payload["no_improve_iterations"]) is int and payload["no_improve_iterations"] >= 0, (
        "no_improve_iterations must be a non-negative integer"
    )


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

        self.assertNotIn(LEGACY_MAX_BATCH_RETRIES_KEY, campaign["execution"])
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
        _validate_backend_payload("enqueue", _read_json("tests/fixtures/backend/enqueue-valid.json"))
        _validate_backend_payload("status", _read_json("tests/fixtures/backend/status-valid.json"))
        _validate_backend_payload("results", _read_json("tests/fixtures/backend/results-valid.json"))
        _validate_state_payload(_read_json("tests/fixtures/state/running.json"))
        _validate_state_payload(_read_json("tests/fixtures/state/complete.json"))
        _validate_state_payload(_read_json("tests/fixtures/state/blocked-protocol.json"))
        _validate_batch_manifest(_read_json("tests/fixtures/manifest/valid.json"))

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

        materialization_slots = [slot for slot in running["active_slots"] if slot["mode"] == "materialization"]
        self.assertEqual(len(materialization_slots), 1)
        self.assertEqual(materialization_slots[0]["model_class"], "strong_coder")

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

        with self.assertRaisesRegex(AssertionError, r"artifacts.data_manifest is required"):
            _validate_batch_manifest(_read_json("tests/fixtures/manifest/invalid-missing-data-manifest.json"))

    def test_batch_manifest_requires_minimal_nested_contract_fields(self) -> None:
        fixture = _read_json("tests/fixtures/manifest/valid.json")

        missing_proposal_id = dict(fixture)
        missing_proposal_id["experiment"] = {"slot_id": "bg-1"}
        with self.assertRaisesRegex(AssertionError, r"experiment.proposal_id is required"):
            _validate_batch_manifest(missing_proposal_id)

        blank_proposal_id = dict(fixture)
        blank_proposal_id["experiment"] = {"proposal_id": ""}
        with self.assertRaisesRegex(AssertionError, r"experiment.proposal_id is required"):
            _validate_batch_manifest(blank_proposal_id)

        missing_max_attempts = dict(fixture)
        missing_max_attempts["retry_policy"] = {"strategy": "retry"}
        with self.assertRaisesRegex(AssertionError, r"retry_policy.max_attempts is required"):
            _validate_batch_manifest(missing_max_attempts)

        zero_max_attempts = dict(fixture)
        zero_max_attempts["retry_policy"] = {"max_attempts": 0}
        with self.assertRaisesRegex(AssertionError, r"retry_policy.max_attempts is required"):
            _validate_batch_manifest(zero_max_attempts)

        boolean_max_attempts = dict(fixture)
        boolean_max_attempts["retry_policy"] = {"max_attempts": True}
        with self.assertRaisesRegex(AssertionError, r"retry_policy.max_attempts is required"):
            _validate_batch_manifest(boolean_max_attempts)

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

        malformed_rounds = dict(fixture)
        malformed_rounds["proposal_cycle"] = dict(fixture["proposal_cycle"])
        malformed_rounds["proposal_cycle"]["ideation_rounds_by_slot"] = {"": 1}
        with self.assertRaisesRegex(
            AssertionError, r"proposal_cycle.ideation_rounds_by_slot keys must be non-empty strings"
        ):
            _validate_state_payload(malformed_rounds)

        negative_rounds = dict(fixture)
        negative_rounds["proposal_cycle"] = dict(fixture["proposal_cycle"])
        negative_rounds["proposal_cycle"]["ideation_rounds_by_slot"] = {"bg-1": -1}
        with self.assertRaisesRegex(
            AssertionError, r"proposal_cycle.ideation_rounds_by_slot values must be non-negative integers"
        ):
            _validate_state_payload(negative_rounds)

        boolean_rounds = dict(fixture)
        boolean_rounds["proposal_cycle"] = dict(fixture["proposal_cycle"])
        boolean_rounds["proposal_cycle"]["ideation_rounds_by_slot"] = {"bg-1": False}
        with self.assertRaisesRegex(
            AssertionError, r"proposal_cycle.ideation_rounds_by_slot values must be non-negative integers"
        ):
            _validate_state_payload(boolean_rounds)

        malformed_patch_artifacts = dict(fixture)
        malformed_patch_artifacts["local_changeset"] = dict(fixture["local_changeset"])
        malformed_patch_artifacts["local_changeset"]["patch_artifacts"] = [
            {
                "producer_slot_id": "",
                "purpose": "review",
                "patch_path": "artifacts/patch.diff",
                "target_worktree": ".ml-metaopt/worktrees/iter-3-materialization",
            }
        ]
        with self.assertRaisesRegex(
            AssertionError, r"patch_artifacts entries must include non-empty producer_slot_id"
        ):
            _validate_state_payload(malformed_patch_artifacts)

        malformed_apply_results = dict(fixture)
        malformed_apply_results["local_changeset"] = dict(fixture["local_changeset"])
        malformed_apply_results["local_changeset"]["apply_results"] = [
            {"patch_path": "", "status": "applied"}
        ]
        with self.assertRaisesRegex(
            AssertionError, r"apply_results entries must include non-empty patch_path"
        ):
            _validate_state_payload(malformed_apply_results)

    def test_state_validator_rejects_malformed_slot_baseline_and_selected_experiment_shapes(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        blank_slot_id = copy.deepcopy(fixture)
        blank_slot_id["active_slots"][0]["slot_id"] = ""
        with self.assertRaisesRegex(AssertionError, r"slot_id is required"):
            _validate_state_payload(blank_slot_id)

        blank_slot_status = copy.deepcopy(fixture)
        blank_slot_status["active_slots"][0]["status"] = ""
        with self.assertRaisesRegex(AssertionError, r"slot status is required"):
            _validate_state_payload(blank_slot_status)

        invalid_slot_attempt = copy.deepcopy(fixture)
        invalid_slot_attempt["active_slots"][0]["attempt"] = False
        with self.assertRaisesRegex(AssertionError, r"slot attempt must be a positive integer"):
            _validate_state_payload(invalid_slot_attempt)

        zero_slot_attempt = copy.deepcopy(fixture)
        zero_slot_attempt["active_slots"][0]["attempt"] = 0
        with self.assertRaisesRegex(AssertionError, r"slot attempt must be a positive integer"):
            _validate_state_payload(zero_slot_attempt)

        boolean_baseline_aggregate = copy.deepcopy(fixture)
        boolean_baseline_aggregate["baseline"]["aggregate"] = True
        with self.assertRaisesRegex(AssertionError, r"baseline.aggregate must be numeric"):
            _validate_state_payload(boolean_baseline_aggregate)

        empty_baseline_by_dataset = copy.deepcopy(fixture)
        empty_baseline_by_dataset["baseline"]["by_dataset"] = {}
        with self.assertRaisesRegex(AssertionError, r"baseline.by_dataset must be a non-empty object"):
            _validate_state_payload(empty_baseline_by_dataset)

        blank_baseline_dataset_key = copy.deepcopy(fixture)
        blank_baseline_dataset_key["baseline"]["by_dataset"] = {"": 0.1269}
        with self.assertRaisesRegex(AssertionError, r"baseline.by_dataset keys must be non-empty strings"):
            _validate_state_payload(blank_baseline_dataset_key)

        boolean_baseline_dataset_value = copy.deepcopy(fixture)
        boolean_baseline_dataset_value["baseline"]["by_dataset"] = {"ds_main": False}
        with self.assertRaisesRegex(AssertionError, r"baseline.by_dataset values must be numeric"):
            _validate_state_payload(boolean_baseline_dataset_value)

        malformed_selected_experiment = copy.deepcopy(fixture)
        malformed_selected_experiment["selected_experiment"] = []
        with self.assertRaisesRegex(AssertionError, r"selected_experiment must be an object"):
            _validate_state_payload(malformed_selected_experiment)

        blank_selected_experiment_proposal = copy.deepcopy(fixture)
        blank_selected_experiment_proposal["selected_experiment"]["proposal_id"] = ""
        with self.assertRaisesRegex(AssertionError, r"selected_experiment.proposal_id is required"):
            _validate_state_payload(blank_selected_experiment_proposal)

        invalid_selected_experiment_attempts = copy.deepcopy(fixture)
        invalid_selected_experiment_attempts["selected_experiment"]["sanity_attempts"] = True
        with self.assertRaisesRegex(
            AssertionError, r"selected_experiment.sanity_attempts must be a non-negative integer"
        ):
            _validate_state_payload(invalid_selected_experiment_attempts)

    def test_state_validator_enforces_selected_experiment_and_local_changeset_lifecycle(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        pre_selection = copy.deepcopy(fixture)
        pre_selection["machine_state"] = "WAIT_FOR_PROPOSAL_THRESHOLD"
        pre_selection["selected_experiment"] = None
        pre_selection["local_changeset"] = None
        _validate_state_payload(pre_selection)

        post_selection_pre_materialization = copy.deepcopy(fixture)
        post_selection_pre_materialization["machine_state"] = "DESIGN_EXPERIMENT"
        post_selection_pre_materialization["local_changeset"] = None
        _validate_state_payload(post_selection_pre_materialization)

        blocked_config = copy.deepcopy(fixture)
        blocked_config["status"] = "BLOCKED_CONFIG"
        blocked_config["machine_state"] = "BLOCKED_CONFIG"
        blocked_config["active_slots"] = []
        blocked_config["selected_experiment"] = None
        blocked_config["local_changeset"] = None
        _validate_state_payload(blocked_config)

        blocked_protocol = copy.deepcopy(fixture)
        blocked_protocol["status"] = "BLOCKED_PROTOCOL"
        blocked_protocol["machine_state"] = "BLOCKED_PROTOCOL"
        blocked_protocol["active_slots"] = []
        blocked_protocol["selected_experiment"] = None
        blocked_protocol["local_changeset"] = None
        _validate_state_payload(blocked_protocol)

        missing_selected_experiment = copy.deepcopy(fixture)
        missing_selected_experiment["selected_experiment"] = None
        with self.assertRaisesRegex(
            AssertionError, r"selected_experiment must be populated once SELECT_EXPERIMENT completes"
        ):
            _validate_state_payload(missing_selected_experiment)

        rollover_cleared_selected_experiment = copy.deepcopy(fixture)
        rollover_cleared_selected_experiment["machine_state"] = "ROLL_ITERATION"
        rollover_cleared_selected_experiment["selected_experiment"] = None
        _validate_state_payload(rollover_cleared_selected_experiment)

        quiesce_cleared_selected_experiment = copy.deepcopy(fixture)
        quiesce_cleared_selected_experiment["machine_state"] = "QUIESCE_SLOTS"
        quiesce_cleared_selected_experiment["selected_experiment"] = None
        _validate_state_payload(quiesce_cleared_selected_experiment)

        complete_cleared_selected_experiment = _read_json("tests/fixtures/state/complete.json")
        complete_cleared_selected_experiment["selected_experiment"] = None
        _validate_state_payload(complete_cleared_selected_experiment)

        missing_local_changeset = copy.deepcopy(fixture)
        missing_local_changeset["local_changeset"] = None
        with self.assertRaisesRegex(
            AssertionError, r"local_changeset must be populated once MATERIALIZE_CHANGESET completes"
        ):
            _validate_state_payload(missing_local_changeset)

    def test_state_validator_rejects_non_strong_coder_materialization_slot(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        invalid_materialization_slot = copy.deepcopy(fixture)
        invalid_materialization_slot["active_slots"][1]["model_class"] = "strong_reasoner"
        with self.assertRaisesRegex(
            AssertionError, r"materialization slots must use model_class strong_coder"
        ):
            _validate_state_payload(invalid_materialization_slot)

    def test_state_validator_rejects_invalid_hash_format_and_remote_batches(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        invalid_campaign_hash = copy.deepcopy(fixture)
        invalid_campaign_hash["campaign_identity_hash"] = "sha256:ABC123"
        with self.assertRaisesRegex(AssertionError, r"campaign_identity_hash must be a sha256 digest"):
            _validate_state_payload(invalid_campaign_hash)

        invalid_runtime_hash = copy.deepcopy(fixture)
        invalid_runtime_hash["runtime_config_hash"] = "not-a-digest"
        with self.assertRaisesRegex(AssertionError, r"runtime_config_hash must be a sha256 digest"):
            _validate_state_payload(invalid_runtime_hash)

        blank_remote_batch_id = copy.deepcopy(fixture)
        blank_remote_batch_id["remote_batches"] = [
            {"batch_id": "", "queue_ref": "queue-20260405-0001", "status": "running"}
        ]
        with self.assertRaisesRegex(AssertionError, r"remote_batches entries must include non-empty batch_id"):
            _validate_state_payload(blank_remote_batch_id)

        malformed_remote_batch = copy.deepcopy(fixture)
        malformed_remote_batch["remote_batches"] = [{"batch_id": "batch-20260405-0001", "queue_ref": "", "status": "running"}]
        with self.assertRaisesRegex(AssertionError, r"remote_batches entries must include non-empty queue_ref"):
            _validate_state_payload(malformed_remote_batch)

        invalid_remote_batch_status = copy.deepcopy(fixture)
        invalid_remote_batch_status["remote_batches"] = [
            {
                "batch_id": "batch-20260405-0001",
                "queue_ref": "queue-20260405-0001",
                "status": "waiting",
            }
        ]
        with self.assertRaisesRegex(AssertionError, r"remote_batches entries must include valid status"):
            _validate_state_payload(invalid_remote_batch_status)

    def test_state_validator_rejects_malformed_completed_experiments(self) -> None:
        fixture = _read_json("tests/fixtures/state/running.json")

        malformed_completed_experiments = copy.deepcopy(fixture)
        malformed_completed_experiments["completed_experiments"] = {}
        with self.assertRaisesRegex(AssertionError, r"completed_experiments must be a list"):
            _validate_state_payload(malformed_completed_experiments)

        non_object_completed_experiment = copy.deepcopy(fixture)
        non_object_completed_experiment["completed_experiments"] = ["batch-20260401-0004"]
        with self.assertRaisesRegex(AssertionError, r"completed_experiments entries must be objects"):
            _validate_state_payload(non_object_completed_experiment)

        blank_completed_experiment_batch_id = copy.deepcopy(fixture)
        blank_completed_experiment_batch_id["completed_experiments"][0]["batch_id"] = ""
        with self.assertRaisesRegex(
            AssertionError, r"completed_experiments entries must include non-empty batch_id"
        ):
            _validate_state_payload(blank_completed_experiment_batch_id)

        boolean_completed_experiment_aggregate = copy.deepcopy(fixture)
        boolean_completed_experiment_aggregate["completed_experiments"][0]["aggregate"] = True
        with self.assertRaisesRegex(AssertionError, r"completed_experiments aggregate must be numeric"):
            _validate_state_payload(boolean_completed_experiment_aggregate)

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

        blank_next_action = copy.deepcopy(fixture)
        blank_next_action["next_action"] = ""
        with self.assertRaisesRegex(AssertionError, r"next_action is required"):
            _validate_state_payload(blank_next_action)

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

        invalid_objective_aggregation = copy.deepcopy(fixture)
        invalid_objective_aggregation["objective_snapshot"] = dict(fixture["objective_snapshot"])
        invalid_objective_aggregation["objective_snapshot"]["aggregation"] = []
        with self.assertRaisesRegex(AssertionError, r"objective_snapshot.aggregation must be an object"):
            _validate_state_payload(invalid_objective_aggregation)

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

    def test_batch_manifest_rejects_non_positive_or_boolean_iteration(self) -> None:
        fixture = _read_json("tests/fixtures/manifest/valid.json")

        zero_iteration = dict(fixture)
        zero_iteration["iteration"] = 0
        with self.assertRaisesRegex(AssertionError, r"iteration is required"):
            _validate_batch_manifest(zero_iteration)

        boolean_iteration = dict(fixture)
        boolean_iteration["iteration"] = True
        with self.assertRaisesRegex(AssertionError, r"iteration is required"):
            _validate_batch_manifest(boolean_iteration)

    def test_results_validator_rejects_boolean_aggregate_value(self) -> None:
        fixture = _read_json("tests/fixtures/backend/results-valid.json")

        boolean_aggregate = copy.deepcopy(fixture)
        boolean_aggregate["best_aggregate_result"]["value"] = True
        with self.assertRaisesRegex(AssertionError, r"aggregate value must be numeric"):
            _validate_backend_payload("results", boolean_aggregate)

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
        delegation_section = skill_md.split("The orchestrator must delegate:")[1].split("## Quick Flow")[0]
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
        _require_pattern(self, contracts, r"Workers never generate `proposal_id`")

    def test_selected_experiment_expanded_shape(self) -> None:
        """selected_experiment must document all lifecycle fields from SELECT through ANALYZE."""
        contracts = _read_text("references/contracts.md")
        _require_pattern(self, contracts, r"proposal_snapshot.*frozen copy")
        _require_pattern(self, contracts, r"selection_rationale.*string")
        _require_pattern(self, contracts, r"design.*object or `null`.*authoritative input for MATERIALIZE")
        _require_pattern(self, contracts, r"diagnosis_history.*array.*ordered list")
        _require_pattern(self, contracts, r"analysis_summary.*object or `null`.*structured analysis")
        _require_pattern(self, contracts, r"clear `selected_experiment`.*ROLL_ITERATION")

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
            "executor_directives",
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
            "enqueue_batch",
            "poll_batch_status",
            "fetch_batch_results",
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
        _require_pattern(self, state_machine, r"executor_directives")

    def test_contracts_document_campaign_started_at(self) -> None:
        """contracts.md must list campaign_started_at as a required state-file key."""
        contracts = _read_text("references/contracts.md")
        self.assertIn("campaign_started_at", contracts)
        # Must appear in the required keys section, not the recommended section
        required_section_end = contracts.index("Recommended additional keys")
        first_occurrence = contracts.index("campaign_started_at")
        self.assertLess(first_occurrence, required_section_end,
                        "campaign_started_at must be in the required keys section")

    def test_state_machine_documents_max_wallclock_hours_in_roll_iteration(self) -> None:
        """state-machine.md ROLL_ITERATION must document max_wallclock_hours stop condition."""
        state_machine = _read_text("references/state-machine.md")
        # The ROLL_ITERATION section should list max_wallclock_hours as a stop condition
        self.assertIn("max_wallclock_hours", state_machine)

    def test_state_machine_documents_terminal_cleanup_directives(self) -> None:
        """state-machine.md terminal states must document explicit executor_directives for cleanup."""
        state_machine = _read_text("references/state-machine.md")
        self.assertIn("executor_directives", state_machine)

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
        """Every control-agent manifest must declare executor_directives as the authoritative executor input."""
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
                "executor_directives",
                content,
                f"{manifest_path} must mention executor_directives",
            )
            _require_pattern(
                self,
                content,
                r"`executor_directives`.*authoritative.*executor",
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
        and the claude-opus-4.6-fast intent for strong_reasoner/strong_coder."""
        guide = _read_text("references/dispatch-guide.md")
        self.assertIn("preferred_model", guide)
        self.assertIn("claude-opus-4.6-fast", guide)

    def test_preferred_model_documented_in_control_protocol(self) -> None:
        """control-protocol.md launch_requests must document preferred_model."""
        protocol = _read_text("references/control-protocol.md")
        self.assertIn("preferred_model", protocol)

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

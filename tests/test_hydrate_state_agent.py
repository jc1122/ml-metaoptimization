from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hydrate_state_handoff.py"
AGENT_PROFILE = ROOT / ".github" / "agents" / "metaopt-hydrate-state.agent.md"
AGENTS_HOOK = textwrap.dedent(
    """\
    <!-- ml-metaoptimization:begin -->
    ## Active task
    `.ml-metaopt/state.json` exists. Inspect its `status`.
    If `status` is `RUNNING`, invoke the `ml-metaoptimization` skill immediately,
    resume from `machine_state`, and do not ask the user for campaign inputs.
    If `status` is terminal, remove this block and follow `next_action` instead of auto-resuming.
    <!-- ml-metaoptimization:end -->
    """
)


class HydrateStateHandoffTests(unittest.TestCase):
    def _write_step1_handoff(self, tempdir: Path, *, outcome: str = "ok") -> Path:
        handoff_path = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "producer": "metaopt-load-campaign",
            "phase": "LOAD_CAMPAIGN",
            "outcome": outcome,
            "campaign_path": str(tempdir / "ml_metaopt_campaign.yaml"),
            "campaign_exists": True,
            "campaign_valid": outcome == "ok",
            "campaign_id": "market-forecast-v3" if outcome == "ok" else None,
            "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c" if outcome == "ok" else None,
            "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6" if outcome == "ok" else None,
            "objective_snapshot": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {
                    "method": "weighted_mean",
                    "weights": {"ds_main": 0.7, "ds_holdout": 0.3},
                },
                "improvement_threshold": 0.0005,
            }
            if outcome == "ok"
            else None,
            "baseline_snapshot": {
                "aggregate": 0.1284,
                "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320},
            }
            if outcome == "ok"
            else None,
            "proposal_policy": {
                "current_target": 8,
                "current_floor": 4,
                "next_cap": 200,
                "distinctness_rule": "non_overlapping",
            }
            if outcome == "ok"
            else None,
            "dispatch_policy": {"background_slots": 8, "auxiliary_slots": 2} if outcome == "ok" else None,
            "validation_issues": [] if outcome == "ok" else ["campaign invalid"],
            "warnings": [],
            "state_peek": {
                "path": str(tempdir / ".ml-metaopt" / "state.json"),
                "exists": False,
                "readable": False,
                "identity_relation": "missing",
                "campaign_identity_hash": None,
            },
            "recommended_next_machine_state": "HYDRATE_STATE" if outcome == "ok" else "BLOCKED_CONFIG",
            "recommended_next_action": "hydrate or initialize orchestrator state" if outcome == "ok" else "repair ml_metaopt_campaign.yaml",
            "summary": "campaign validated; hand off to HYDRATE_STATE" if outcome == "ok" else "campaign invalid",
        }
        handoff_path.write_text(json.dumps(payload), encoding="utf-8")
        return handoff_path

    def _write_skills_manifest(self, tempdir: Path, *, missing_required: bool = False, missing_degradable: bool = False) -> Path:
        skills_manifest = tempdir / "agents" / "worker-skills.json"
        skills_manifest.parent.mkdir(parents=True, exist_ok=True)

        required_probe = tempdir / ".github" / "agents" / "metaopt-ideation-worker.agent.md"
        if not missing_required:
            required_probe.parent.mkdir(parents=True, exist_ok=True)
            required_probe.write_text("ok\n", encoding="utf-8")

        degradable_probe = tempdir / "skills" / "repo-audit-refactor-optimize" / "SKILL.md"
        if not missing_degradable:
            degradable_probe.parent.mkdir(parents=True, exist_ok=True)
            degradable_probe.write_text("ok\n", encoding="utf-8")

        payload = {
            "skills": [
                {
                    "name": "metaopt-ideation-worker",
                    "lane": "ideation",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-ideation-worker",
                    "classification": "required",
                    "probe_paths": [str(required_probe)],
                },
                {
                    "name": "repo-audit-refactor-optimize",
                    "lane": "maintenance",
                    "worker_kind": "skill",
                    "worker_ref": "repo-audit-refactor-optimize",
                    "classification": "degradable",
                    "degraded_lane": "maintenance",
                    "probe_paths": [str(degradable_probe)],
                },
            ]
        }
        skills_manifest.write_text(json.dumps(payload), encoding="utf-8")
        return skills_manifest

    def _write_full_skills_manifest(self, tempdir: Path) -> Path:
        skills_manifest = tempdir / "agents" / "worker-skills.json"
        skills_manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "skills": [
                {
                    "name": "metaopt-ideation-worker",
                    "lane": "ideation",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-ideation-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-ideation-worker.agent.md")],
                },
                {
                    "name": "metaopt-selection-worker",
                    "lane": "selection",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-selection-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-selection-worker.agent.md")],
                },
                {
                    "name": "metaopt-design-worker",
                    "lane": "design",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-design-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-design-worker.agent.md")],
                },
                {
                    "name": "metaopt-materialization-worker",
                    "lane": "materialization",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-materialization-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-materialization-worker.agent.md")],
                },
                {
                    "name": "metaopt-diagnosis-worker",
                    "lane": "diagnosis",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-diagnosis-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-diagnosis-worker.agent.md")],
                },
                {
                    "name": "metaopt-analysis-worker",
                    "lane": "analysis",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-analysis-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-analysis-worker.agent.md")],
                },
                {
                    "name": "metaopt-rollover-worker",
                    "lane": "rollover",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-rollover-worker",
                    "classification": "degradable",
                    "degraded_lane": "rollover",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-rollover-worker.agent.md")],
                },
                {
                    "name": "repo-audit-refactor-optimize",
                    "lane": "maintenance",
                    "worker_kind": "skill",
                    "worker_ref": "repo-audit-refactor-optimize",
                    "classification": "degradable",
                    "degraded_lane": "maintenance",
                    "probe_paths": ["/home/jakub/.agents/skills/repo-audit-refactor-optimize/SKILL.md"],
                },
            ]
        }
        skills_manifest.write_text(json.dumps(payload), encoding="utf-8")
        return skills_manifest

    def _run_tool(
        self,
        tempdir: Path,
        *,
        handoff_outcome: str = "ok",
        missing_required: bool = False,
        missing_degradable: bool = False,
        state_payload: dict | str | None = None,
        agents_text: str | None = None,
        full_manifest: bool = False,
    ) -> tuple[dict, Path, Path]:
        handoff_path = self._write_step1_handoff(tempdir, outcome=handoff_outcome)
        if full_manifest:
            skills_manifest = self._write_full_skills_manifest(tempdir)
        else:
            skills_manifest = self._write_skills_manifest(
                tempdir,
                missing_required=missing_required,
                missing_degradable=missing_degradable,
            )

        state_path = tempdir / ".ml-metaopt" / "state.json"
        if state_payload is not None:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(state_payload, str):
                state_path.write_text(state_payload, encoding="utf-8")
            else:
                state_path.write_text(json.dumps(state_payload), encoding="utf-8")

        agents_path = tempdir / "AGENTS.md"
        if agents_text is not None:
            agents_path.write_text(agents_text, encoding="utf-8")

        output_path = tempdir / ".ml-metaopt" / "handoffs" / "hydrate_state.latest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        completed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--load-handoff",
                str(handoff_path),
                "--state-path",
                str(state_path),
                "--agents-path",
                str(agents_path),
                "--skills-manifest",
                str(skills_manifest),
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}",
        )
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, json.loads(completed.stdout))
        return payload, state_path, agents_path

    def test_fresh_init_writes_running_state_and_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, agents_path = self._run_tool(Path(tempdir_str))

            self.assertEqual(payload["outcome"], "initialized")
            self.assertEqual(payload["effective_machine_state"], "MAINTAIN_BACKGROUND_POOL")
            self.assertTrue(payload["state_written"])
            self.assertEqual(payload["agents_hook_action"], "created")
            self.assertTrue(state_path.exists())
            self.assertEqual(agents_path.read_text(encoding="utf-8"), AGENTS_HOOK)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "RUNNING")
            self.assertEqual(state["machine_state"], "MAINTAIN_BACKGROUND_POOL")
            self.assertEqual(state["current_iteration"], 1)
            self.assertEqual(state["next_action"], "maintain background slot pool")
            self.assertEqual(state["current_proposals"], [])
            self.assertEqual(state["next_proposals"], [])
            self.assertEqual(state["runtime_capabilities"]["available_skills"], ["metaopt-ideation-worker", "repo-audit-refactor-optimize"])
            self.assertEqual(state["runtime_capabilities"]["missing_skills"], [])

    def test_resume_matching_state_updates_runtime_capabilities_and_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            existing_state = {
                "version": 3,
                "campaign_id": "market-forecast-v3",
                "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
                "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
                "status": "RUNNING",
                "machine_state": "WAIT_FOR_PROPOSAL_THRESHOLD",
                "current_iteration": 3,
                "next_action": "wait for enough proposals",
                "objective_snapshot": {
                    "metric": "rmse",
                    "direction": "minimize",
                    "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                    "improvement_threshold": 0.0005,
                },
                "proposal_cycle": {
                    "cycle_id": "iter-3-cycle-1",
                    "current_pool_frozen": False,
                    "ideation_rounds_by_slot": {},
                    "shortfall_reason": "not_enough_proposals",
                },
                "active_slots": [],
                "current_proposals": [],
                "next_proposals": [],
                "selected_experiment": None,
                "local_changeset": None,
                "remote_batches": [],
                "baseline": {"aggregate": 0.1284, "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320}},
                "completed_experiments": [],
                "key_learnings": [],
                "no_improve_iterations": 0,
                "runtime_capabilities": {
                    "verified_at": "2026-04-06T00:00:00Z",
                    "available_skills": [],
                    "missing_skills": [],
                    "degraded_lanes": [],
                },
            }
            payload, state_path, agents_path = self._run_tool(
                Path(tempdir_str),
                state_payload=existing_state,
                agents_text="# Notes\n",
            )

            self.assertEqual(payload["outcome"], "resumed")
            self.assertEqual(payload["resume_mode"], "existing")
            self.assertEqual(payload["effective_machine_state"], "WAIT_FOR_PROPOSAL_THRESHOLD")
            self.assertEqual(payload["agents_hook_action"], "updated")
            self.assertIn(AGENTS_HOOK, agents_path.read_text(encoding="utf-8"))

            resumed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(resumed["machine_state"], "WAIT_FOR_PROPOSAL_THRESHOLD")
            self.assertEqual(resumed["runtime_capabilities"]["available_skills"], ["metaopt-ideation-worker", "repo-audit-refactor-optimize"])

    def test_fresh_init_with_full_worker_manifest_records_all_required_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, _ = self._run_tool(Path(tempdir_str), full_manifest=True)

            self.assertEqual(payload["outcome"], "initialized")
            capabilities = payload["runtime_capabilities"]
            self.assertEqual(
                capabilities["available_skills"],
                [
                    "metaopt-analysis-worker",
                    "metaopt-design-worker",
                    "metaopt-diagnosis-worker",
                    "metaopt-ideation-worker",
                    "metaopt-materialization-worker",
                    "metaopt-rollover-worker",
                    "metaopt-selection-worker",
                    "repo-audit-refactor-optimize",
                ],
            )
            self.assertEqual(capabilities["missing_skills"], [])
            self.assertEqual(capabilities["degraded_lanes"], [])

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["runtime_capabilities"], capabilities)

    def test_identity_mismatch_preserves_state_and_removes_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            stale_state = {
                "version": 3,
                "campaign_id": "old-campaign",
                "campaign_identity_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
                "status": "RUNNING",
                "machine_state": "MAINTAIN_BACKGROUND_POOL",
                "current_iteration": 1,
                "next_action": "maintain background slot pool",
                "objective_snapshot": {"metric": "rmse", "direction": "minimize", "aggregation": {}, "improvement_threshold": 0.0005},
                "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": False, "ideation_rounds_by_slot": {}, "shortfall_reason": ""},
                "active_slots": [],
                "current_proposals": [],
                "next_proposals": [],
                "selected_experiment": None,
                "local_changeset": None,
                "remote_batches": [],
                "baseline": {"aggregate": 0.1284, "by_dataset": {"ds_main": 0.1269}},
                "completed_experiments": [],
                "key_learnings": [],
                "no_improve_iterations": 0,
                "runtime_capabilities": {"verified_at": "2026-04-06T00:00:00Z", "available_skills": [], "missing_skills": [], "degraded_lanes": []},
            }
            original = json.dumps(stale_state, sort_keys=True)
            payload, state_path, agents_path = self._run_tool(
                Path(tempdir_str),
                state_payload=stale_state,
                agents_text=AGENTS_HOOK + "\nOther notes\n",
            )

            self.assertEqual(payload["outcome"], "blocked_config")
            self.assertFalse(payload["state_written"])
            self.assertTrue(payload["state_preserved"])
            self.assertEqual(payload["agents_hook_action"], "removed")
            self.assertEqual(payload["recommended_next_action"], "archive or remove the stale state before starting a new campaign")
            self.assertEqual(json.dumps(json.loads(state_path.read_text(encoding="utf-8")), sort_keys=True), original)
            self.assertNotIn("ml-metaoptimization:begin", agents_path.read_text(encoding="utf-8"))

    def test_missing_required_skill_writes_blocked_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, agents_path = self._run_tool(
                Path(tempdir_str),
                missing_required=True,
                agents_text=AGENTS_HOOK,
            )

            self.assertEqual(payload["outcome"], "blocked_config")
            self.assertEqual(payload["effective_machine_state"], "BLOCKED_CONFIG")
            self.assertTrue(payload["state_written"])
            self.assertEqual(payload["agents_hook_action"], "removed")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "BLOCKED_CONFIG")
            self.assertEqual(state["machine_state"], "BLOCKED_CONFIG")
            self.assertEqual(state["next_action"], "install missing skill: metaopt-ideation-worker")
            self.assertEqual(state["active_slots"], [])
            self.assertEqual(state["runtime_capabilities"]["missing_skills"], ["metaopt-ideation-worker"])
            self.assertNotIn("ml-metaoptimization:begin", agents_path.read_text(encoding="utf-8"))

    def test_missing_degradable_skill_continues_with_degraded_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, _ = self._run_tool(
                Path(tempdir_str),
                missing_degradable=True,
            )

            self.assertEqual(payload["outcome"], "initialized")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "RUNNING")
            self.assertEqual(state["runtime_capabilities"]["missing_skills"], ["repo-audit-refactor-optimize"])
            self.assertEqual(state["runtime_capabilities"]["degraded_lanes"], ["maintenance"])

    def test_invalid_step1_handoff_returns_runtime_error_without_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, agents_path = self._run_tool(
                Path(tempdir_str),
                handoff_outcome="blocked_config",
            )

            self.assertEqual(payload["outcome"], "runtime_error")
            self.assertFalse(payload["state_written"])
            self.assertFalse(state_path.exists())
            self.assertFalse(agents_path.exists())

    def test_malformed_existing_state_returns_runtime_error_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, _ = self._run_tool(
                Path(tempdir_str),
                state_payload="{not-json",
            )

            self.assertEqual(payload["outcome"], "runtime_error")
            self.assertFalse(payload["state_written"])
            self.assertTrue(state_path.exists())
            self.assertEqual(state_path.read_text(encoding="utf-8"), "{not-json")

    def test_fresh_init_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run_tool(Path(tempdir_str))

            self.assertEqual(payload["handoff_type"], "HYDRATE_STATE")
            self.assertEqual(payload["control_agent"], "metaopt-hydrate-state")
            self.assertEqual(payload["launch_requests"], [])
            self.assertEqual(payload["state_patch"], {})
            self.assertEqual(payload["executor_directives"], [])
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_runtime_error_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run_tool(Path(tempdir_str), handoff_outcome="blocked_config")

            self.assertEqual(payload["handoff_type"], "HYDRATE_STATE")
            self.assertEqual(payload["control_agent"], "metaopt-hydrate-state")
            self.assertEqual(payload["launch_requests"], [])
            self.assertEqual(payload["state_patch"], {})
            self.assertEqual(payload["executor_directives"], [])

    def test_identity_mismatch_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            stale_state = {
                "version": 3,
                "campaign_id": "old-campaign",
                "campaign_identity_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
                "status": "RUNNING",
                "machine_state": "MAINTAIN_BACKGROUND_POOL",
                "current_iteration": 1,
                "next_action": "maintain background slot pool",
                "objective_snapshot": {"metric": "rmse", "direction": "minimize", "aggregation": {}, "improvement_threshold": 0.0005},
                "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": False, "ideation_rounds_by_slot": {}, "shortfall_reason": ""},
                "active_slots": [],
                "current_proposals": [],
                "next_proposals": [],
                "selected_experiment": None,
                "local_changeset": None,
                "remote_batches": [],
                "baseline": {"aggregate": 0.1284, "by_dataset": {"ds_main": 0.1269}},
                "completed_experiments": [],
                "key_learnings": [],
                "no_improve_iterations": 0,
                "runtime_capabilities": {"verified_at": "2026-04-06T00:00:00Z", "available_skills": [], "missing_skills": [], "degraded_lanes": []},
            }
            payload, _, _ = self._run_tool(Path(tempdir_str), state_payload=stale_state)

            self.assertEqual(payload["handoff_type"], "HYDRATE_STATE")
            self.assertEqual(payload["control_agent"], "metaopt-hydrate-state")
            self.assertEqual(payload["launch_requests"], [])
            self.assertEqual(payload["state_patch"], {})
            self.assertEqual(payload["executor_directives"], [])

    def test_fresh_init_sets_campaign_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, _ = self._run_tool(Path(tempdir_str))

            self.assertEqual(payload["outcome"], "initialized")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("campaign_started_at", state)
            self.assertRegex(state["campaign_started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_resume_preserves_campaign_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            existing_state = {
                "version": 3,
                "campaign_id": "market-forecast-v3",
                "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
                "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
                "status": "RUNNING",
                "machine_state": "WAIT_FOR_PROPOSAL_THRESHOLD",
                "current_iteration": 3,
                "next_action": "wait for enough proposals",
                "objective_snapshot": {
                    "metric": "rmse",
                    "direction": "minimize",
                    "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                    "improvement_threshold": 0.0005,
                },
                "proposal_cycle": {
                    "cycle_id": "iter-3-cycle-1",
                    "current_pool_frozen": False,
                    "ideation_rounds_by_slot": {},
                    "shortfall_reason": "not_enough_proposals",
                },
                "active_slots": [],
                "current_proposals": [],
                "next_proposals": [],
                "selected_experiment": None,
                "local_changeset": None,
                "remote_batches": [],
                "baseline": {"aggregate": 0.1284, "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320}},
                "completed_experiments": [],
                "key_learnings": [],
                "no_improve_iterations": 0,
                "runtime_capabilities": {
                    "verified_at": "2026-04-06T00:00:00Z",
                    "available_skills": [],
                    "missing_skills": [],
                    "degraded_lanes": [],
                },
                "campaign_started_at": "2026-04-01T10:00:00Z",
            }
            payload, state_path, _ = self._run_tool(
                Path(tempdir_str),
                state_payload=existing_state,
            )

            self.assertEqual(payload["outcome"], "resumed")
            resumed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(resumed["campaign_started_at"], "2026-04-01T10:00:00Z")

    def test_resume_defaults_campaign_started_at_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            existing_state = {
                "version": 3,
                "campaign_id": "market-forecast-v3",
                "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
                "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
                "status": "RUNNING",
                "machine_state": "WAIT_FOR_PROPOSAL_THRESHOLD",
                "current_iteration": 3,
                "next_action": "wait for enough proposals",
                "objective_snapshot": {
                    "metric": "rmse",
                    "direction": "minimize",
                    "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                    "improvement_threshold": 0.0005,
                },
                "proposal_cycle": {
                    "cycle_id": "iter-3-cycle-1",
                    "current_pool_frozen": False,
                    "ideation_rounds_by_slot": {},
                    "shortfall_reason": "not_enough_proposals",
                },
                "active_slots": [],
                "current_proposals": [],
                "next_proposals": [],
                "selected_experiment": None,
                "local_changeset": None,
                "remote_batches": [],
                "baseline": {"aggregate": 0.1284, "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320}},
                "completed_experiments": [],
                "key_learnings": [],
                "no_improve_iterations": 0,
                "runtime_capabilities": {
                    "verified_at": "2026-04-06T00:00:00Z",
                    "available_skills": [],
                    "missing_skills": [],
                    "degraded_lanes": [],
                },
            }
            payload, state_path, _ = self._run_tool(
                Path(tempdir_str),
                state_payload=existing_state,
            )

            self.assertEqual(payload["outcome"], "resumed")
            resumed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("campaign_started_at", resumed)
            self.assertRegex(resumed["campaign_started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_agent_profile_exists_and_is_programmatic_only(self) -> None:
        self.assertTrue(AGENT_PROFILE.exists(), f"missing {AGENT_PROFILE}")
        content = AGENT_PROFILE.read_text(encoding="utf-8")

        self.assertIn("name: metaopt-hydrate-state", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("tools:", content)
        self.assertIn("user-invocable: false", content)
        self.assertIn("scripts/hydrate_state_handoff.py", content)


if __name__ == "__main__":
    unittest.main()

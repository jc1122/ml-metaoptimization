from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "background_control_handoff.py"
AGENT_PROFILE = ROOT / ".github" / "agents" / "metaopt-background-control.agent.md"


class BackgroundControlAgentTests(unittest.TestCase):
    def _write_load_handoff(self, tempdir: Path) -> Path:
        handoff = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "producer": "metaopt-load-campaign",
            "phase": "LOAD_CAMPAIGN",
            "outcome": "ok",
            "campaign_id": "market-forecast-v3",
            "goal": "Improve out-of-sample forecast quality without temporal leakage.",
            "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
            "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
            "objective_snapshot": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                "improvement_threshold": 0.0005,
            },
            "baseline_snapshot": {
                "aggregate": 0.1284,
                "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320},
            },
            "proposal_policy": {
                "current_target": 3,
                "current_floor": 2,
                "next_cap": 5,
                "distinctness_rule": "non_overlapping",
            },
            "dispatch_policy": {"background_slots": 2, "auxiliary_slots": 2},
            "warnings": [],
            "summary": "ok",
        }
        handoff.write_text(json.dumps(payload), encoding="utf-8")
        return handoff

    def _base_state(self) -> dict:
        return {
            "version": 3,
            "campaign_id": "market-forecast-v3",
            "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
            "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
            "status": "RUNNING",
            "machine_state": "MAINTAIN_BACKGROUND_POOL",
            "current_iteration": 1,
            "next_action": "maintain background slot pool",
            "objective_snapshot": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                "improvement_threshold": 0.0005,
            },
            "proposal_cycle": {
                "cycle_id": "iter-1-cycle-1",
                "current_pool_frozen": False,
                "ideation_rounds_by_slot": {},
                "shortfall_reason": "",
            },
            "active_slots": [],
            "current_proposals": [],
            "next_proposals": [],
            "selected_experiment": None,
            "local_changeset": None,
            "remote_batches": [],
            "baseline": {
                "aggregate": 0.1284,
                "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320},
            },
            "completed_experiments": [],
            "key_learnings": [],
            "no_improve_iterations": 0,
            "runtime_capabilities": {
                "verified_at": "2026-04-06T00:00:00Z",
                "available_skills": ["metaopt-ideation-worker", "repo-audit-refactor-optimize"],
                "missing_skills": [],
                "degraded_lanes": [],
            },
        }

    def _run(
        self,
        tempdir: Path,
        *,
        mode: str,
        state: dict,
        slot_events: dict[str, dict] | None = None,
        worker_results: dict[str, dict] | None = None,
    ) -> tuple[dict, dict, Path]:
        load_handoff = self._write_load_handoff(tempdir)
        state_path = tempdir / ".ml-metaopt" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        tasks_dir = tempdir / ".ml-metaopt" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        worker_results_dir = tempdir / ".ml-metaopt" / "worker-results"
        worker_results_dir.mkdir(parents=True, exist_ok=True)
        slot_events_dir = tempdir / ".ml-metaopt" / "slot-events"
        slot_events_dir.mkdir(parents=True, exist_ok=True)
        output_path = tempdir / ".ml-metaopt" / "handoffs" / f"{mode}.latest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        for slot_id, payload in (slot_events or {}).items():
            (slot_events_dir / f"{slot_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        for slot_id, payload in (worker_results or {}).items():
            (worker_results_dir / f"{slot_id}.json").write_text(json.dumps(payload), encoding="utf-8")

        completed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--mode",
                mode,
                "--load-handoff",
                str(load_handoff),
                "--state-path",
                str(state_path),
                "--tasks-dir",
                str(tasks_dir),
                "--worker-results-dir",
                str(worker_results_dir),
                "--slot-events-dir",
                str(slot_events_dir),
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
        updated_state = json.loads(state_path.read_text(encoding="utf-8"))
        return payload, updated_state, tasks_dir

    def test_plan_mode_emits_launch_requests_and_task_files_for_missing_background_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, tasks_dir = self._run(
                Path(tempdir_str),
                mode="plan_background_work",
                state=self._base_state(),
            )

            self.assertEqual(payload["phase"], "PLAN_BACKGROUND_WORK")
            self.assertEqual(payload["pool_status"], "building")
            self.assertEqual(payload["recommended_next_machine_state"], "MAINTAIN_BACKGROUND_POOL")
            self.assertEqual(len(payload["launch_requests"]), 2)
            self.assertEqual(payload["launch_requests"][0]["worker_kind"], "custom_agent")
            self.assertEqual(payload["launch_requests"][0]["worker_ref"], "metaopt-ideation-worker")
            self.assertEqual(updated_state["active_slots"][0]["mode"], "ideation")
            self.assertEqual(updated_state["active_slots"][1]["mode"], "ideation")
            task_file = tasks_dir / "bg-1.md"
            self.assertTrue(task_file.exists())
            content = task_file.read_text(encoding="utf-8")
            self.assertIn("metaopt-ideation-worker", content)
            self.assertIn("Goal:", content)
            self.assertIn("Current Proposal Pool:", content)
            self.assertIn("Output Schema", content)

    def test_plan_mode_switches_to_maintenance_when_next_pool_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["next_proposals"] = [{}, {}, {}, {}, {}]
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="plan_background_work",
                state=state,
            )

            self.assertEqual(payload["launch_requests"][0]["mode"], "maintenance")
            self.assertEqual(payload["launch_requests"][0]["worker_kind"], "skill")
            self.assertEqual(payload["launch_requests"][0]["worker_ref"], "repo-audit-refactor-optimize")
            self.assertEqual(updated_state["active_slots"][0]["mode"], "maintenance")

    def test_plan_mode_can_return_ready_without_launches(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["current_proposals"] = [{"proposal_id": "market-forecast-v3-p1"}, {"proposal_id": "market-forecast-v3-p2"}, {"proposal_id": "market-forecast-v3-p3"}]
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="plan_background_work",
                state=state,
            )

            self.assertEqual(payload["pool_status"], "ready")
            self.assertEqual(payload["recommended_next_machine_state"], "SELECT_EXPERIMENT")
            self.assertEqual(payload["launch_requests"], [])
            self.assertEqual(updated_state["current_proposals"], state["current_proposals"])

    def test_gate_mode_enriches_completed_ideation_results_and_loops_when_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["active_slots"] = [
                {
                    "slot_id": "bg-1",
                    "slot_class": "background",
                    "mode": "ideation",
                    "model_class": "general_worker",
                    "requested_model": "Auto",
                    "resolved_model": "Auto",
                    "status": "running",
                    "attempt": 1,
                    "task_summary": "Generate proposals",
                }
            ]
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_background_work",
                state=state,
                slot_events={
                    "bg-1": {"slot_id": "bg-1", "status": "completed", "result_file": "bg-1.json"}
                },
                worker_results={
                    "bg-1": {
                        "slot_id": "bg-1",
                        "mode": "ideation",
                        "status": "completed",
                        "summary": "two candidates",
                        "proposal_candidates": [
                            {
                                "title": "Tighten rolling split",
                                "rationale": "Lower leakage risk",
                                "expected_impact": {"direction": "improve", "magnitude": "medium"},
                                "target_area": "validation",
                            },
                            {
                                "title": "Add lag features",
                                "rationale": "Improve signal",
                                "expected_impact": {"direction": "improve", "magnitude": "small"},
                                "target_area": "features",
                            },
                        ],
                    }
                },
            )

            self.assertEqual(payload["phase"], "GATE_BACKGROUND_WORK")
            self.assertEqual(payload["pool_status"], "building")
            self.assertEqual(payload["recommended_next_machine_state"], "MAINTAIN_BACKGROUND_POOL")
            self.assertEqual(updated_state["proposal_cycle"]["ideation_rounds_by_slot"]["bg-1"], 1)
            self.assertEqual(len(updated_state["current_proposals"]), 2)
            self.assertEqual(updated_state["current_proposals"][0]["proposal_id"], "market-forecast-v3-p1")
            self.assertEqual(updated_state["proposal_cycle"]["shortfall_reason"], "not_enough_proposals")

    def test_gate_mode_can_advance_via_floor_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["current_proposals"] = [{"proposal_id": "market-forecast-v3-p1"}, {"proposal_id": "market-forecast-v3-p2"}]
            state["proposal_cycle"]["ideation_rounds_by_slot"] = {"bg-1": 2, "bg-2": 2}
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_background_work",
                state=state,
            )

            self.assertEqual(payload["pool_status"], "ready")
            self.assertEqual(payload["recommended_next_machine_state"], "SELECT_EXPERIMENT")
            self.assertEqual(updated_state["proposal_cycle"]["shortfall_reason"], "")

    def test_plan_mode_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_background_work",
                state=self._base_state(),
            )

            self.assertEqual(payload["handoff_type"], "PLAN_BACKGROUND_WORK")
            self.assertEqual(payload["control_agent"], "metaopt-background-control")
            self.assertIsInstance(payload["launch_requests"], list)
            self.assertEqual(payload["state_patch"], {})
            self.assertEqual(payload["executor_directives"], [])
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_gate_mode_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["current_proposals"] = [{"proposal_id": "market-forecast-v3-p1"}, {"proposal_id": "market-forecast-v3-p2"}]
            state["proposal_cycle"]["ideation_rounds_by_slot"] = {"bg-1": 2, "bg-2": 2}
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_background_work",
                state=state,
            )

            self.assertEqual(payload["handoff_type"], "GATE_BACKGROUND_WORK")
            self.assertEqual(payload["control_agent"], "metaopt-background-control")
            self.assertEqual(payload["launch_requests"], [])
            self.assertEqual(payload["state_patch"], {})
            self.assertEqual(payload["executor_directives"], [])
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_plan_launch_request_has_legal_slot_class_mode_and_preferred_model(self) -> None:
        """Background launch requests must carry legal slot_class, mode, and preferred_model."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_background_work",
                state=self._base_state(),
            )

            self.assertGreater(len(payload["launch_requests"]), 0)
            for lr in payload["launch_requests"]:
                self.assertEqual(lr["slot_class"], "background")
                self.assertIn(lr["mode"], {"ideation", "maintenance"})
                self.assertIn("preferred_model", lr)
                self.assertIsInstance(lr["preferred_model"], str)
                self.assertTrue(lr["preferred_model"])
                # Legal worker tuple
                self.assertIn("worker_kind", lr)
                self.assertIn("worker_ref", lr)

    def test_gate_returns_blocked_protocol_when_ideation_result_leaks_lane_fields(self) -> None:
        """If ideation output contains semantic-lane fields, gate must fail closed with BLOCKED_PROTOCOL."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["active_slots"] = [
                {
                    "slot_id": "bg-1",
                    "slot_class": "background",
                    "mode": "ideation",
                    "model_class": "general_worker",
                    "requested_model": "Auto",
                    "resolved_model": "Auto",
                    "status": "running",
                    "attempt": 1,
                    "task_summary": "Generate proposals",
                }
            ]
            # Ideation result that has leaked materialization-lane fields
            leaked_result = {
                "slot_id": "bg-1",
                "mode": "ideation",
                "status": "completed",
                "summary": "drifted into materialization",
                "proposal_candidates": [
                    {"title": "Something", "rationale": "Something"},
                ],
                "code_changes": [{"path": "src/foo.py", "intent": "fix bug"}],
                "patch_artifacts": [{"file": "src/foo.py", "diff": "--- a\n+++ b"}],
            }
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_background_work",
                state=state,
                slot_events={
                    "bg-1": {"slot_id": "bg-1", "status": "completed", "result_file": "bg-1.json"},
                },
                worker_results={"bg-1": leaked_result},
            )

            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_PROTOCOL")
            # Proposals must NOT have been enriched
            self.assertEqual(len(updated_state["current_proposals"]), 0)

    def test_agent_profile_exists_and_declares_both_modes(self) -> None:
        self.assertTrue(AGENT_PROFILE.exists(), f"missing {AGENT_PROFILE}")
        content = AGENT_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-background-control", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("plan_background_work", content)
        self.assertIn("gate_background_work", content)
        self.assertIn("scripts/background_control_handoff.py", content)


if __name__ == "__main__":
    unittest.main()

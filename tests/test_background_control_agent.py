from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "background_control_handoff.py"


def _v4_state(**overrides):
    base = {
        "version": 4,
        "campaign_id": "test-campaign",
        "campaign_identity_hash": "sha256:" + "a" * 64,
        "status": "RUNNING",
        "machine_state": "IDEATE",
        "current_iteration": 1,
        "next_action": "maintain background pool",
        "objective_snapshot": {
            "metric": "val/accuracy",
            "direction": "maximize",
            "improvement_threshold": 0.005,
        },
        "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": False},
        "current_sweep": None,
        "selected_sweep": None,
        "baseline": None,
        "current_proposals": [],
        "next_proposals": [],
        "key_learnings": [],
        "completed_iterations": [],
        "no_improve_iterations": 0,
        "campaign_started_at": "2026-04-13T07:00:00Z",
    }
    base.update(overrides)
    return base


def _v4_load_handoff(**overrides):
    base = {
        "schema_version": 1,
        "handoff_type": "load_campaign.validate",
        "control_agent": "metaopt-load-campaign",
        "campaign_id": "test-campaign",
        "campaign_valid": True,
        "campaign_identity_hash": "sha256:" + "a" * 64,
        "objective_snapshot": {"metric": "val/accuracy", "direction": "maximize", "improvement_threshold": 0.005},
        "compute": {"provider": "vast_ai", "accelerator": "A100:1", "num_sweep_agents": 4, "idle_timeout_minutes": 15, "max_budget_usd": 10},
        "wandb": {"entity": "my-entity", "project": "my-project"},
        "project": {"repo": "git@github.com:org/repo.git", "smoke_test_command": "python train.py --smoke"},
        "proposal_policy": {"current_target": 5},
        "stop_conditions": {"max_iterations": 20, "target_metric": 0.99, "max_no_improve_iterations": 5},
        "recommended_next_machine_state": "HYDRATE_STATE",
        "state_patch": None,
        "directives": [],
        "warnings": [],
        "summary": "ok",
    }
    base.update(overrides)
    return base


class BackgroundControlAgentTests(unittest.TestCase):

    def _run(self, tempdir, mode, *, state=None, load_handoff=None, secondary=False):
        tmp = Path(tempdir)
        state_path = tmp / ".ml-metaopt" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if state is not None:
            state_path.write_text(json.dumps(state), encoding="utf-8")
        load_h_path = tmp / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        load_h_path.parent.mkdir(parents=True, exist_ok=True)
        load_h_path.write_text(json.dumps(load_handoff or _v4_load_handoff()), encoding="utf-8")
        tasks_dir = tmp / ".ml-metaopt" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        worker_results_dir = tmp / ".ml-metaopt" / "worker-results"
        worker_results_dir.mkdir(parents=True, exist_ok=True)
        output_path = tmp / "handoff.json"
        cmd = [
            "python3", str(SCRIPT),
            "--mode", mode,
            "--load-handoff", str(load_h_path),
            "--state-path", str(state_path),
            "--tasks-dir", str(tasks_dir),
            "--worker-results-dir", str(worker_results_dir),
            "--output", str(output_path),
            "--apply-state",
        ]
        if secondary:
            cmd.append("--secondary")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT / "scripts"))
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
        payload = json.loads(output_path.read_text()) if output_path.exists() else {}
        updated_state = json.loads(state_path.read_text()) if state_path.exists() else {}
        return payload, updated_state, result

    # ── plan_background_work ───────────────────────────────────────────

    def test_plan_work_below_threshold_emits_launch_requests(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, updated, _ = self._run(td, "plan_background_work", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "WAIT_FOR_PROPOSALS")
            self.assertEqual(payload["pool_status"], "building")
            self.assertGreater(len(payload["launch_requests"]), 0)
            for lr in payload["launch_requests"]:
                self.assertEqual(lr["worker_ref"], "metaopt-ideation-worker")

    def test_plan_work_at_threshold_recommends_selection(self):
        with tempfile.TemporaryDirectory() as td:
            proposals = [{"proposal_id": f"test-campaign-p{i}", "title": f"P{i}"} for i in range(5)]
            state = _v4_state(current_proposals=proposals)
            payload, _, _ = self._run(td, "plan_background_work", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "SELECT_AND_DESIGN_SWEEP")
            self.assertEqual(payload["pool_status"], "ready")
            self.assertEqual(len(payload["launch_requests"]), 0)

    def test_plan_work_secondary_nullifies_recommended_state(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_background_work", state=state, secondary=True)
            self.assertIsNone(payload["recommended_next_machine_state"])

    def test_plan_work_writes_task_files(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_background_work", state=state)
            task_files = list((tmp / ".ml-metaopt" / "tasks").glob("bg-*.md"))
            self.assertEqual(len(task_files), len(payload["launch_requests"]))

    def test_plan_work_unfreezes_pool(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            state["proposal_cycle"]["current_pool_frozen"] = True
            _, updated, _ = self._run(td, "plan_background_work", state=state)
            self.assertFalse(updated["proposal_cycle"]["current_pool_frozen"])

    # ── gate_background_work ───────────────────────────────────────────

    def test_gate_work_results_fill_pool_recommends_selection(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(current_proposals=[
                {"proposal_id": f"test-campaign-p{i}", "title": f"P{i}"} for i in range(3)
            ])
            for i in range(1, 4):
                rp = tmp / ".ml-metaopt" / "worker-results" / f"bg-{i}.json"
                rp.parent.mkdir(parents=True, exist_ok=True)
                rp.write_text(json.dumps({
                    "status": "completed",
                    "proposal_candidates": [{"title": f"New P{i}", "sweep_config": {}}],
                }))
            payload, updated, _ = self._run(td, "gate_background_work", state=state)
            self.assertGreaterEqual(len(updated["current_proposals"]), 5)
            self.assertEqual(payload["pool_status"], "ready")
            self.assertEqual(payload["recommended_next_machine_state"], "SELECT_AND_DESIGN_SWEEP")

    def test_gate_work_below_threshold_recommends_ideate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "bg-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "status": "completed",
                "proposal_candidates": [{"title": "One proposal"}],
            }))
            payload, updated, _ = self._run(td, "gate_background_work", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "IDEATE")
            self.assertEqual(payload["pool_status"], "building")

    def test_gate_work_enriches_proposals_with_ids(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "bg-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "status": "completed",
                "proposal_candidates": [{"title": "P"}],
            }))
            _, updated, _ = self._run(td, "gate_background_work", state=state)
            self.assertEqual(len(updated["current_proposals"]), 1)
            p = updated["current_proposals"][0]
            self.assertTrue(p["proposal_id"].startswith("test-campaign-p"))
            self.assertEqual(p["source_slot_id"], "bg-1")
            self.assertIn("created_at", p)

    def test_gate_work_secondary_nullifies_recommended_state(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "gate_background_work", state=state, secondary=True)
            self.assertIsNone(payload["recommended_next_machine_state"])

    def test_gate_work_frozen_pool_appends_to_next_proposals(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            state["proposal_cycle"]["current_pool_frozen"] = True
            rp = tmp / ".ml-metaopt" / "worker-results" / "bg-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "status": "completed",
                "proposal_candidates": [{"title": "P"}],
            }))
            _, updated, _ = self._run(td, "gate_background_work", state=state)
            self.assertEqual(len(updated["next_proposals"]), 1)
            self.assertEqual(len(updated["current_proposals"]), 0)

    # ── envelope ───────────────────────────────────────────────────────

    def test_plan_work_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_background_work", state=state)
            self.assertEqual(payload["handoff_type"], "background_control.plan_background_work")
            self.assertEqual(payload["control_agent"], "metaopt-background-control")
            self.assertIn("state_patch", payload)
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_gate_work_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "gate_background_work", state=state)
            self.assertIn("current_proposal_count", payload)
            self.assertIn("next_proposal_count", payload)
            self.assertIn("processed_results", payload)

    def test_launch_requests_include_preferred_model(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_background_work", state=state)
            for lr in payload["launch_requests"]:
                self.assertIn("preferred_model", lr)

    def test_no_results_gate_still_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "gate_background_work", state=state)
            self.assertIsNotNone(payload["recommended_next_machine_state"])
            self.assertEqual(payload["processed_results"], [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "select_and_design_handoff.py"


def _v4_state(**overrides):
    base = {
        "version": 4,
        "campaign_id": "test-campaign",
        "campaign_identity_hash": "sha256:" + "a" * 64,
        "status": "RUNNING",
        "machine_state": "SELECT_AND_DESIGN_SWEEP",
        "current_iteration": 1,
        "next_action": "select experiment",
        "objective_snapshot": {
            "metric": "val/accuracy",
            "direction": "maximize",
            "improvement_threshold": 0.005,
        },
        "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": True},
        "current_sweep": None,
        "selected_sweep": None,
        "baseline": {"metric": "val/accuracy", "value": 0.923, "wandb_run_id": "run-001", "wandb_run_url": "https://wandb.ai/x", "established_at": "2026-04-13T08:00:00Z"},
        "current_proposals": [
            {"proposal_id": "test-campaign-p1", "title": "Higher LR", "sweep_config": {"method": "grid"}},
            {"proposal_id": "test-campaign-p2", "title": "Deeper net", "sweep_config": {"method": "bayes"}},
        ],
        "next_proposals": [],
        "key_learnings": [],
        "completed_iterations": [],
        "no_improve_iterations": 0,
        "campaign_started_at": "2026-04-13T07:00:00Z",
    }
    base.update(overrides)
    return base


def _v4_load_handoff():
    return {
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


class SelectDesignAgentTests(unittest.TestCase):

    def _run(self, tempdir, mode, *, state=None, load_handoff=None):
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
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT / "scripts"))
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
        payload = json.loads(output_path.read_text()) if output_path.exists() else {}
        updated_state = json.loads(state_path.read_text()) if state_path.exists() else {}
        return payload, updated_state, result

    # ── plan_select_design ─────────────────────────────────────────────

    def test_plan_select_design_validates_proposals_and_freezes_pool(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, updated, _ = self._run(td, "plan_select_design", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "SELECT_AND_DESIGN_SWEEP")
            # No worker dispatch — selection is done inline by metaopt-select-design agent
            self.assertEqual(len(payload["launch_requests"]), 0)
            self.assertTrue(updated["proposal_cycle"]["current_pool_frozen"])

    def test_plan_select_design_writes_task_file(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_select_design", state=state)
            task_files = list((tmp / ".ml-metaopt" / "tasks").glob("select-design-*.md"))
            self.assertEqual(len(task_files), 1)

    def test_plan_select_design_empty_proposals_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state(current_proposals=[])
            payload, _, _ = self._run(td, "plan_select_design", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("empty", payload["summary"].lower())

    def test_plan_select_design_selected_sweep_already_set_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state(selected_sweep={"proposal_id": "old", "sweep_config": {}})
            payload, _, _ = self._run(td, "plan_select_design", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("already populated", payload["summary"])

    # ── finalize_select_design ─────────────────────────────────────────

    def test_finalize_select_design_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            result_path = tmp / ".ml-metaopt" / "worker-results" / "select-design-iter-1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({
                "winning_proposal": {"proposal_id": "test-campaign-p1"},
                "sweep_config": {"method": "grid", "parameters": {"lr": {"values": [0.01]}}},
                "ranking_rationale": "best fit",
            }))
            payload, updated, _ = self._run(td, "finalize_select_design", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "LOCAL_SANITY")
            self.assertEqual(updated["selected_sweep"]["proposal_id"], "test-campaign-p1")
            self.assertIsNotNone(updated["selected_sweep"]["sweep_config"])

    def test_finalize_select_design_missing_result_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "finalize_select_design", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("missing", payload["summary"].lower())

    def test_finalize_select_design_unknown_winner_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            result_path = tmp / ".ml-metaopt" / "worker-results" / "select-design-iter-1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({
                "winning_proposal": {"proposal_id": "nonexistent-p99"},
                "sweep_config": {"method": "grid"},
            }))
            payload, _, _ = self._run(td, "finalize_select_design", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("does not match", payload["summary"])

    def test_finalize_select_design_missing_sweep_config_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            result_path = tmp / ".ml-metaopt" / "worker-results" / "select-design-iter-1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({
                "winning_proposal": {"proposal_id": "test-campaign-p1"},
            }))
            payload, _, _ = self._run(td, "finalize_select_design", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("sweep_config", payload["summary"])

    def test_finalize_select_design_already_selected_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(selected_sweep={"proposal_id": "old", "sweep_config": {}})
            result_path = tmp / ".ml-metaopt" / "worker-results" / "select-design-iter-1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({
                "winning_proposal": {"proposal_id": "test-campaign-p1"},
                "sweep_config": {"method": "grid"},
            }))
            payload, _, _ = self._run(td, "finalize_select_design", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])

    # ── envelope ───────────────────────────────────────────────────────

    def test_plan_select_design_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_select_design", state=state)
            self.assertEqual(payload["handoff_type"], "select_design.plan_select_design")
            self.assertEqual(payload["control_agent"], "metaopt-select-design")
            self.assertIn("state_patch", payload)
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_runtime_error_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            bad_load = {"control_agent": "wrong", "campaign_valid": False}
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_select_design", state=state, load_handoff=bad_load)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("handoff_type", payload)
            self.assertIn("control_agent", payload)
            self.assertIn("summary", payload)

    def test_plan_select_design_no_worker_dispatch(self):
        """plan_select_design should not dispatch any workers — selection is inline."""
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_select_design", state=state)
            self.assertEqual(payload["launch_requests"], [])


if __name__ == "__main__":
    unittest.main()

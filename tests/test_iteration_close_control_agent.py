from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "iteration_close_control_handoff.py"


def _v4_state(**overrides):
    base = {
        "version": 4,
        "campaign_id": "test-campaign",
        "campaign_identity_hash": "sha256:" + "a" * 64,
        "status": "RUNNING",
        "machine_state": "ROLL_ITERATION",
        "current_iteration": 1,
        "next_action": "roll iteration",
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
            {"proposal_id": "test-campaign-p1", "title": "P1"},
            {"proposal_id": "test-campaign-p2", "title": "P2"},
        ],
        "next_proposals": [
            {"proposal_id": "test-campaign-p3", "title": "P3"},
        ],
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


class IterationCloseControlAgentTests(unittest.TestCase):

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

    # ── plan_roll_iteration ────────────────────────────────────────────

    def test_plan_roll_iteration_emits_rollover_task(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_roll_iteration", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "ROLL_ITERATION")
            task_files = list((tmp / ".ml-metaopt" / "tasks").glob("rollover-iter-*.md"))
            self.assertEqual(len(task_files), 1)

    def test_plan_roll_iteration_emits_launch_request(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_roll_iteration", state=state)
            self.assertEqual(len(payload["launch_requests"]), 1)
            lr = payload["launch_requests"][0]
            self.assertEqual(lr["worker_ref"], "metaopt-analysis-worker")
            self.assertEqual(lr["model_class"], "strong_reasoner")

    def test_plan_roll_iteration_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_roll_iteration", state=state)
            self.assertEqual(payload["handoff_type"], "iteration_close.plan_roll_iteration")
            self.assertEqual(payload["control_agent"], "metaopt-iteration-close-control")
            self.assertIn("state_patch", payload)
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)
            self.assertEqual(payload["directives"], [])

    # ── gate_roll_iteration ────────────────────────────────────────────

    def test_gate_roll_iteration_continues_to_ideate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [{"proposal_id": "test-campaign-p1", "title": "P1"}],
                "merged_proposals": [],
                "needs_fresh_ideation": True,
                "summary": "filtered proposals carried forward",
            }))
            payload, updated, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "IDEATE")
            self.assertTrue(payload["continue_campaign"])
            self.assertEqual(updated["current_iteration"], 2)
            self.assertIsNone(updated["selected_sweep"])
            self.assertIsNone(updated["current_sweep"])

    def test_gate_roll_iteration_stops_on_max_iterations(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(current_iteration=20)
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-20.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [],
                "needs_fresh_ideation": False,
                "summary": "done",
            }))
            payload, updated, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "COMPLETE")
            self.assertFalse(payload["continue_campaign"])
            self.assertEqual(payload["stop_reason"], "max_iterations")

    def test_gate_roll_iteration_stops_on_no_improve(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(no_improve_iterations=5)
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [],
                "needs_fresh_ideation": False,
                "summary": "done",
            }))
            payload, _, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "COMPLETE")
            self.assertEqual(payload["stop_reason"], "max_no_improve_iterations")

    def test_gate_roll_iteration_stops_on_target_metric(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(baseline={"metric": "val/accuracy", "value": 0.995, "wandb_run_id": "r", "wandb_run_url": "u", "established_at": "t"})
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [],
                "needs_fresh_ideation": False,
                "summary": "done",
            }))
            payload, updated, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "COMPLETE")
            self.assertEqual(payload["stop_reason"], "target_metric")
            # Should NOT increment iteration when stopping
            self.assertEqual(updated["current_iteration"], 1)

    def test_gate_roll_iteration_enriches_merged_proposals(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [{"title": "New merged P"}],
                "needs_fresh_ideation": True,
                "summary": "merged in",
            }))
            _, updated, _ = self._run(td, "gate_roll_iteration", state=state)
            merged = [p for p in updated["current_proposals"] if p.get("source_slot_id") == "rollover"]
            self.assertEqual(len(merged), 1)
            self.assertTrue(merged[0]["proposal_id"].startswith("test-campaign-p"))

    def test_gate_roll_iteration_missing_result_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("missing", payload["summary"].lower())

    def test_gate_roll_iteration_malformed_result_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({"some_field": "no required keys"}))
            payload, _, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])

    def test_gate_roll_iteration_continuing_emits_iteration_report_directive(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [],
                "needs_fresh_ideation": True,
                "summary": "ok",
            }))
            payload, _, _ = self._run(td, "gate_roll_iteration", state=state)
            actions = [d["action"] for d in payload["directives"]]
            self.assertIn("emit_iteration_report", actions)
            self.assertNotIn("emit_final_report", actions)
            self.assertNotIn("remove_agents_hook", actions)

    def test_gate_roll_iteration_stopping_emits_terminal_directives(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(current_iteration=20)
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-20.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [],
                "needs_fresh_ideation": False,
                "summary": "done",
            }))
            payload, _, _ = self._run(td, "gate_roll_iteration", state=state)
            actions = [d["action"] for d in payload["directives"]]
            self.assertIn("emit_iteration_report", actions)
            self.assertIn("emit_final_report", actions)
            self.assertIn("remove_agents_hook", actions)

    def test_gate_roll_iteration_preserves_sweep_cleared_state(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [],
                "needs_fresh_ideation": True,
                "summary": "ok",
            }))
            _, updated, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertIsNone(updated["selected_sweep"])
            self.assertIsNone(updated["current_sweep"])

    def test_gate_roll_iteration_clears_next_proposals(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [{"proposal_id": "test-campaign-p1", "title": "P1"}],
                "merged_proposals": [],
                "needs_fresh_ideation": True,
                "summary": "ok",
            }))
            _, updated, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertEqual(updated["next_proposals"], [])

    def test_runtime_error_on_invalid_load_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            bad = {"control_agent": "wrong"}
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_roll_iteration", state=state, load_handoff=bad)
            self.assertIsNone(payload["recommended_next_machine_state"])

    def test_iteration_report_present_on_gate_success(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            rp = tmp / ".ml-metaopt" / "worker-results" / "rollover-iter-1.json"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "filtered_proposals": [],
                "merged_proposals": [],
                "needs_fresh_ideation": True,
                "summary": "ok",
            }))
            payload, _, _ = self._run(td, "gate_roll_iteration", state=state)
            self.assertIsNotNone(payload.get("iteration_report"))
            self.assertIn("Iteration 1", payload["iteration_report"])


if __name__ == "__main__":
    unittest.main()

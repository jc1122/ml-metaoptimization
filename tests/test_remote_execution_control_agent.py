from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "remote_execution_control_handoff.py"


def _v4_state(**overrides):
    base = {
        "version": 4,
        "campaign_id": "test-campaign",
        "campaign_identity_hash": "sha256:" + "a" * 64,
        "status": "RUNNING",
        "machine_state": "LOCAL_SANITY",
        "current_iteration": 1,
        "next_action": "run smoke test",
        "objective_snapshot": {
            "metric": "val/accuracy",
            "direction": "maximize",
            "improvement_threshold": 0.005,
        },
        "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": True},
        "current_sweep": None,
        "selected_sweep": {
            "proposal_id": "prop-001",
            "sweep_config": {
                "method": "bayes",
                "metric": {"name": "val/accuracy", "goal": "maximize"},
                "parameters": {"lr": {"distribution": "log_uniform_values", "min": 1e-4, "max": 1e-2}},
            },
        },
        "baseline": {"metric": "val/accuracy", "value": 0.923, "wandb_run_id": "run-001", "wandb_run_url": "https://wandb.ai/x", "established_at": "2026-04-13T08:00:00Z"},
        "current_proposals": [],
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


class RemoteExecutionControlAgentTests(unittest.TestCase):

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
        executor_events_dir = tmp / ".ml-metaopt" / "executor-events"
        executor_events_dir.mkdir(parents=True, exist_ok=True)
        output_path = tmp / "handoff.json"
        cmd = [
            "python3", str(SCRIPT),
            "--mode", mode,
            "--load-handoff", str(load_h_path),
            "--state-path", str(state_path),
            "--tasks-dir", str(tasks_dir),
            "--worker-results-dir", str(worker_results_dir),
            "--executor-events-dir", str(executor_events_dir),
            "--output", str(output_path),
            "--apply-state",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT / "scripts"))
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
        payload = json.loads(output_path.read_text()) if output_path.exists() else {}
        updated_state = json.loads(state_path.read_text()) if state_path.exists() else {}
        return payload, updated_state, result

    # ── gate_local_sanity ──────────────────────────────────────────────

    def test_gate_local_sanity_passes_and_recommends_launch_sweep(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            smoke = tmp / ".ml-metaopt" / "executor-events" / "smoke-test-iter-1.json"
            smoke.parent.mkdir(parents=True, exist_ok=True)
            smoke.write_text(json.dumps({"exit_code": 0, "timed_out": False}))
            payload, updated, _ = self._run(td, "gate_local_sanity", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "LAUNCH_SWEEP")
            self.assertIn("passed", payload["summary"])

    def test_gate_local_sanity_fails_on_nonzero_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            smoke = tmp / ".ml-metaopt" / "executor-events" / "smoke-test-iter-1.json"
            smoke.parent.mkdir(parents=True, exist_ok=True)
            smoke.write_text(json.dumps({"exit_code": 1, "timed_out": False}))
            payload, _, _ = self._run(td, "gate_local_sanity", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "FAILED")

    def test_gate_local_sanity_fails_on_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            smoke = tmp / ".ml-metaopt" / "executor-events" / "smoke-test-iter-1.json"
            smoke.parent.mkdir(parents=True, exist_ok=True)
            smoke.write_text(json.dumps({"exit_code": 0, "timed_out": True}))
            payload, _, _ = self._run(td, "gate_local_sanity", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "FAILED")
            self.assertIn("timed out", payload["summary"])

    def test_gate_local_sanity_missing_smoke_result_returns_runtime_error(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "gate_local_sanity", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "LAUNCH_SWEEP")
            directives = payload.get("directives", [])
            self.assertTrue(len(directives) >= 1)
            self.assertEqual(directives[0]["action"], "run_smoke_test")

    # ── plan_launch ────────────────────────────────────────────────────

    def test_plan_launch_emits_launch_sweep_directive(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "plan_launch", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "LAUNCH_SWEEP")
            self.assertEqual(len(payload["directives"]), 1)
            d = payload["directives"][0]
            self.assertEqual(d["action"], "launch_sweep")
            self.assertIn("sweep_config", d)
            self.assertIn("sky_task_spec", d)
            self.assertEqual(d["sky_task_spec"]["provider"], "vast_ai")

    def test_plan_launch_missing_selected_sweep_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state(selected_sweep=None)
            payload, _, _ = self._run(td, "plan_launch", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("missing", payload["summary"].lower())

    # ── poll_sweep ─────────────────────────────────────────────────────

    def test_poll_sweep_missing_current_sweep_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state(current_sweep=None)
            payload, _, _ = self._run(td, "poll_sweep", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])

    def test_poll_sweep_no_poll_file_emits_directive(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state(current_sweep={"sweep_id": "sw-1", "sky_job_ids": ["job-1"]})
            payload, _, _ = self._run(td, "poll_sweep", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertEqual(len(payload["directives"]), 1)
            self.assertEqual(payload["directives"][0]["action"], "poll_sweep")

    def test_poll_sweep_running_returns_null(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(current_sweep={"sweep_id": "sw-1", "sky_job_ids": ["job-1"]})
            poll = tmp / ".ml-metaopt" / "executor-events" / "poll-sweep-iter-1.json"
            poll.parent.mkdir(parents=True, exist_ok=True)
            poll.write_text(json.dumps({"sweep_status": "running"}))
            payload, _, _ = self._run(td, "poll_sweep", state=state)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("still running", payload["summary"])

    def test_poll_sweep_completed_advances_to_analyze(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(current_sweep={"sweep_id": "sw-1", "sky_job_ids": ["job-1"]})
            poll = tmp / ".ml-metaopt" / "executor-events" / "poll-sweep-iter-1.json"
            poll.parent.mkdir(parents=True, exist_ok=True)
            poll.write_text(json.dumps({"sweep_status": "completed"}))
            payload, _, _ = self._run(td, "poll_sweep", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "ANALYZE")

    def test_poll_sweep_budget_exceeded_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(current_sweep={"sweep_id": "sw-1", "sky_job_ids": ["job-1"]})
            poll = tmp / ".ml-metaopt" / "executor-events" / "poll-sweep-iter-1.json"
            poll.parent.mkdir(parents=True, exist_ok=True)
            poll.write_text(json.dumps({"sweep_status": "budget_exceeded"}))
            payload, _, _ = self._run(td, "poll_sweep", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")

    def test_poll_sweep_failed_goes_to_failed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state(current_sweep={"sweep_id": "sw-1", "sky_job_ids": ["job-1"]})
            poll = tmp / ".ml-metaopt" / "executor-events" / "poll-sweep-iter-1.json"
            poll.parent.mkdir(parents=True, exist_ok=True)
            poll.write_text(json.dumps({"sweep_status": "failed"}))
            payload, _, _ = self._run(td, "poll_sweep", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "FAILED")

    # ── analyze ────────────────────────────────────────────────────────

    def test_analyze_missing_result_emits_analysis_worker_launch(self):
        with tempfile.TemporaryDirectory() as td:
            state = _v4_state()
            payload, _, _ = self._run(td, "analyze", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "ANALYZE")
            self.assertEqual(len(payload["launch_requests"]), 1)
            lr = payload["launch_requests"][0]
            self.assertEqual(lr["worker_ref"], "metaopt-analysis-worker")

    def test_analyze_improved_updates_baseline_and_rolls(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            analysis = tmp / ".ml-metaopt" / "worker-results" / "sweep-analysis-iter-1.json"
            analysis.parent.mkdir(parents=True, exist_ok=True)
            analysis.write_text(json.dumps({
                "improved": True,
                "best_metric_value": 0.95,
                "best_run_id": "run-best",
                "best_run_url": "https://wandb.ai/best",
                "learnings": ["use higher lr"],
            }))
            payload, updated, _ = self._run(td, "analyze", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "ROLL_ITERATION")
            self.assertEqual(updated["baseline"]["value"], 0.95)
            self.assertEqual(updated["no_improve_iterations"], 0)
            self.assertEqual(len(updated["completed_iterations"]), 1)
            self.assertIn("use higher lr", updated["key_learnings"])

    def test_analyze_not_improved_increments_no_improve(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            analysis = tmp / ".ml-metaopt" / "worker-results" / "sweep-analysis-iter-1.json"
            analysis.parent.mkdir(parents=True, exist_ok=True)
            analysis.write_text(json.dumps({"improved": False, "best_metric_value": 0.91}))
            payload, updated, _ = self._run(td, "analyze", state=state)
            self.assertEqual(payload["recommended_next_machine_state"], "ROLL_ITERATION")
            self.assertEqual(updated["no_improve_iterations"], 1)
            # Baseline unchanged
            self.assertEqual(updated["baseline"]["value"], 0.923)

    # ── envelope ───────────────────────────────────────────────────────

    def test_envelope_keys_present(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            state = _v4_state()
            smoke = tmp / ".ml-metaopt" / "executor-events" / "smoke-test-iter-1.json"
            smoke.parent.mkdir(parents=True, exist_ok=True)
            smoke.write_text(json.dumps({"exit_code": 0, "timed_out": False}))
            payload, _, _ = self._run(td, "gate_local_sanity", state=state)
            self.assertIn("handoff_type", payload)
            self.assertIn("control_agent", payload)
            self.assertEqual(payload["control_agent"], "metaopt-remote-execution-control")
            self.assertIn("state_patch", payload)
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_runtime_error_on_invalid_load_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            bad_load = {"control_agent": "wrong", "campaign_valid": False}
            state = _v4_state()
            payload, _, _ = self._run(td, "gate_local_sanity", state=state, load_handoff=bad_load)
            self.assertIsNone(payload["recommended_next_machine_state"])


if __name__ == "__main__":
    unittest.main()

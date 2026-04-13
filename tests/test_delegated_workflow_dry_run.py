"""v3 delegated workflow dry-run tests — replaced by v4 individual script tests."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

ROOT = Path(__file__).resolve().parents[1]
LOAD_SCRIPT = ROOT / "scripts" / "load_campaign_handoff.py"
HYDRATE_SCRIPT = ROOT / "scripts" / "hydrate_state_handoff.py"
BACKGROUND_SCRIPT = ROOT / "scripts" / "background_control_handoff.py"
SELECT_SCRIPT = ROOT / "scripts" / "select_and_design_handoff.py"
REMOTE_SCRIPT = ROOT / "scripts" / "remote_execution_control_handoff.py"
ITER_CLOSE_SCRIPT = ROOT / "scripts" / "iteration_close_control_handoff.py"


class DelegatedWorkflowDryRunTests(unittest.TestCase):
    """Smoke tests for v4 control-protocol scripts.

    These validate that the scripts can be invoked end-to-end with v4
    CLI args and produce well-formed handoff JSON.
    """

    def _run_load(self, tempdir: Path) -> dict:
        campaign_path = tempdir / "ml_metaopt_campaign.yaml"
        campaign_path.write_text(
            (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        state_path = tempdir / ".ml-metaopt" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        output_path = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write a valid preflight artifact
        preflight_path = tempdir / ".ml-metaopt" / "preflight-readiness.json"
        preflight_path.write_text(json.dumps({
            "schema_version": 1,
            "status": "READY",
            "campaign_id": "gnn-mnist-optimization",
            "campaign_identity_hash": "sha256:76d87a271abe95ba765ac749237a1383dd4c75956671de88f15bba9cca13bc81",
            "emitted_at": "2025-01-01T00:00:00Z",
            "preflight_duration_seconds": 1.0,
            "checks_summary": {"total": 1, "passed": 1, "failed": 0, "bootstrapped": 0},
            "failures": [],
            "next_action": "proceed",
            "diagnostics": None,
        }), encoding="utf-8")

        r = subprocess.run(
            ["python3", str(LOAD_SCRIPT), "--campaign-path", str(campaign_path),
             "--state-path", str(state_path), "--output", str(output_path)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(output_path.read_text(encoding="utf-8"))

    def test_load_campaign_produces_valid_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            payload = self._run_load(Path(td))
            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["control_agent"], "metaopt-load-campaign")
            self.assertTrue(payload["campaign_valid"])
            self.assertEqual(payload["recommended_next_machine_state"], "HYDRATE_STATE")

    def test_emit_handoff_defaults_to_empty_directives(self):
        with tempfile.TemporaryDirectory() as td:
            payload = self._run_load(Path(td))
            self.assertIsInstance(payload.get("directives", []), list)

    def test_load_handoff_contains_v4_compute_fields(self):
        with tempfile.TemporaryDirectory() as td:
            payload = self._run_load(Path(td))
            self.assertIn("compute", payload)
            self.assertIn("wandb", payload)
            self.assertIn("project", payload)

    def test_emit_handoff_includes_state_patch_key(self):
        with tempfile.TemporaryDirectory() as td:
            payload = self._run_load(Path(td))
            self.assertIn("state_patch", payload)

    def test_load_handoff_includes_proposal_policy_and_stop_conditions(self):
        with tempfile.TemporaryDirectory() as td:
            payload = self._run_load(Path(td))
            self.assertIsInstance(payload.get("proposal_policy"), dict)
            self.assertIsInstance(payload.get("stop_conditions"), dict)

    def test_background_script_accepts_v4_cli_args(self):
        """Verify background_control_handoff.py runs with v4 args (no --slot-events-dir)."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            load_h = self._run_load(tmp)
            load_h_path = tmp / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
            state_path = tmp / ".ml-metaopt" / "state.json"
            state_path.write_text(json.dumps({
                "version": 4, "campaign_id": "gnn-mnist-optimization",
                "campaign_identity_hash": load_h["campaign_identity_hash"],
                "status": "RUNNING", "machine_state": "IDEATE", "current_iteration": 1,
                "next_action": "maintain background pool",
                "objective_snapshot": load_h["objective_snapshot"],
                "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": False},
                "current_sweep": None, "selected_sweep": None, "baseline": None,
                "current_proposals": [], "next_proposals": [], "key_learnings": [],
                "completed_iterations": [], "no_improve_iterations": 0,
                "campaign_started_at": "2026-04-13T07:00:00Z",
            }))
            tasks_dir = tmp / ".ml-metaopt" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            wr = tmp / ".ml-metaopt" / "worker-results"
            wr.mkdir(parents=True, exist_ok=True)
            out = tmp / "bg-handoff.json"
            r = subprocess.run(
                ["python3", str(BACKGROUND_SCRIPT), "--mode", "plan_background_work",
                 "--load-handoff", str(load_h_path), "--state-path", str(state_path),
                 "--tasks-dir", str(tasks_dir), "--worker-results-dir", str(wr),
                 "--output", str(out), "--apply-state"],
                capture_output=True, text=True, cwd=str(ROOT / "scripts"),
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            payload = json.loads(out.read_text())
            self.assertIn("launch_requests", payload)

    def test_select_design_script_accepts_v4_cli_args(self):
        """Verify select_and_design_handoff.py runs with v4 args."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            load_h = self._run_load(tmp)
            load_h_path = tmp / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
            state_path = tmp / ".ml-metaopt" / "state.json"
            state_path.write_text(json.dumps({
                "version": 4, "campaign_id": "gnn-mnist-optimization",
                "campaign_identity_hash": load_h["campaign_identity_hash"],
                "status": "RUNNING", "machine_state": "SELECT_AND_DESIGN_SWEEP", "current_iteration": 1,
                "next_action": "select experiment",
                "objective_snapshot": load_h["objective_snapshot"],
                "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": True},
                "current_sweep": None, "selected_sweep": None, "baseline": None,
                "current_proposals": [{"proposal_id": "p1"}, {"proposal_id": "p2"}],
                "next_proposals": [], "key_learnings": [],
                "completed_iterations": [], "no_improve_iterations": 0,
                "campaign_started_at": "2026-04-13T07:00:00Z",
            }))
            tasks_dir = tmp / ".ml-metaopt" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            wr = tmp / ".ml-metaopt" / "worker-results"
            wr.mkdir(parents=True, exist_ok=True)
            out = tmp / "sd-handoff.json"
            r = subprocess.run(
                ["python3", str(SELECT_SCRIPT), "--mode", "plan_select_design",
                 "--load-handoff", str(load_h_path), "--state-path", str(state_path),
                 "--tasks-dir", str(tasks_dir), "--worker-results-dir", str(wr),
                 "--output", str(out), "--apply-state"],
                capture_output=True, text=True, cwd=str(ROOT / "scripts"),
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            payload = json.loads(out.read_text())
            self.assertIn("launch_requests", payload)

    def test_remote_exec_script_accepts_v4_cli_args(self):
        """Verify remote_execution_control_handoff.py runs with v4 args."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            load_h = self._run_load(tmp)
            load_h_path = tmp / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
            state_path = tmp / ".ml-metaopt" / "state.json"
            state_path.write_text(json.dumps({
                "version": 4, "campaign_id": "gnn-mnist-optimization",
                "campaign_identity_hash": load_h["campaign_identity_hash"],
                "status": "RUNNING", "machine_state": "LOCAL_SANITY", "current_iteration": 1,
                "next_action": "run smoke test",
                "objective_snapshot": load_h["objective_snapshot"],
                "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": True},
                "current_sweep": None,
                "selected_sweep": {"proposal_id": "p1", "sweep_config": {"method": "bayes"}},
                "baseline": None,
                "current_proposals": [], "next_proposals": [], "key_learnings": [],
                "completed_iterations": [], "no_improve_iterations": 0,
                "campaign_started_at": "2026-04-13T07:00:00Z",
            }))
            for d in ("tasks", "worker-results", "executor-events"):
                (tmp / ".ml-metaopt" / d).mkdir(parents=True, exist_ok=True)
            # Write smoke test result
            (tmp / ".ml-metaopt" / "executor-events" / "smoke-test-iter-1.json").write_text(
                json.dumps({"exit_code": 0, "timed_out": False})
            )
            out = tmp / "re-handoff.json"
            r = subprocess.run(
                ["python3", str(REMOTE_SCRIPT), "--mode", "gate_local_sanity",
                 "--load-handoff", str(load_h_path), "--state-path", str(state_path),
                 "--tasks-dir", str(tmp / ".ml-metaopt" / "tasks"),
                 "--worker-results-dir", str(tmp / ".ml-metaopt" / "worker-results"),
                 "--executor-events-dir", str(tmp / ".ml-metaopt" / "executor-events"),
                 "--output", str(out), "--apply-state"],
                capture_output=True, text=True, cwd=str(ROOT / "scripts"),
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            payload = json.loads(out.read_text())
            self.assertEqual(payload["recommended_next_machine_state"], "LAUNCH_SWEEP")

    def test_iteration_close_script_accepts_v4_cli_args(self):
        """Verify iteration_close_control_handoff.py runs with v4 args (no --executor-events-dir)."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            load_h = self._run_load(tmp)
            load_h_path = tmp / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
            state_path = tmp / ".ml-metaopt" / "state.json"
            state_path.write_text(json.dumps({
                "version": 4, "campaign_id": "gnn-mnist-optimization",
                "campaign_identity_hash": load_h["campaign_identity_hash"],
                "status": "RUNNING", "machine_state": "ROLL_ITERATION", "current_iteration": 1,
                "next_action": "roll iteration",
                "objective_snapshot": load_h["objective_snapshot"],
                "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": True},
                "current_sweep": None, "selected_sweep": None, "baseline": None,
                "current_proposals": [], "next_proposals": [], "key_learnings": [],
                "completed_iterations": [], "no_improve_iterations": 0,
                "campaign_started_at": "2026-04-13T07:00:00Z",
            }))
            tasks_dir = tmp / ".ml-metaopt" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            wr = tmp / ".ml-metaopt" / "worker-results"
            wr.mkdir(parents=True, exist_ok=True)
            out = tmp / "ic-handoff.json"
            r = subprocess.run(
                ["python3", str(ITER_CLOSE_SCRIPT), "--mode", "plan_roll_iteration",
                 "--load-handoff", str(load_h_path), "--state-path", str(state_path),
                 "--tasks-dir", str(tasks_dir), "--worker-results-dir", str(wr),
                 "--output", str(out), "--apply-state"],
                capture_output=True, text=True, cwd=str(ROOT / "scripts"),
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            payload = json.loads(out.read_text())
            self.assertEqual(payload["handoff_type"], "iteration_close.plan_roll_iteration")


if __name__ == "__main__":
    unittest.main()

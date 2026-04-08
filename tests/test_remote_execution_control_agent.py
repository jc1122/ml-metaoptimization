from __future__ import annotations

from datetime import datetime, timezone
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "remote_execution_control_handoff.py"
AGENT_PROFILE = ROOT / ".github" / "agents" / "metaopt-remote-execution-control.agent.md"
ANALYSIS_WORKER_PROFILE = ROOT / ".github" / "agents" / "metaopt-analysis-worker.agent.md"


class RemoteExecutionControlAgentTests(unittest.TestCase):
    def _today_batch_id(self, sequence: int = 2) -> str:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"batch-{today}-{sequence:04d}"

    def _write_load_handoff(self, tempdir: Path, *, malformed: bool = False) -> Path:
        handoff = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        if malformed:
            handoff.write_text("{not-json", encoding="utf-8")
            return handoff
        payload = {
            "schema_version": 1,
            "producer": "metaopt-load-campaign",
            "phase": "LOAD_CAMPAIGN",
            "outcome": "ok",
            "campaign_id": "market-forecast-v3",
            "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
            "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
            "objective_snapshot": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                "improvement_threshold": 0.0005,
            },
            "remote_queue": {
                "backend": "ray-hetzner",
                "retry_policy": {"max_attempts": 2},
                "enqueue_command": "python3 /opt/ray-hetzner/metaopt/enqueue_batch.py --manifest",
                "status_command": "python3 /opt/ray-hetzner/metaopt/get_batch_status.py --batch-id",
                "results_command": "python3 /opt/ray-hetzner/metaopt/fetch_batch_results.py --batch-id",
            },
            "execution": {
                "runner_type": "ray_queue_runner",
                "entrypoint": "python3 /srv/metaopt/project/scripts/ray_runner.py",
            },
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
            "machine_state": "ENQUEUE_REMOTE_BATCH",
            "current_iteration": 1,
            "next_action": "enqueue remote batch",
            "objective_snapshot": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                "improvement_threshold": 0.0005,
            },
            "proposal_cycle": {
                "cycle_id": "iter-1-cycle-1",
                "current_pool_frozen": True,
                "ideation_rounds_by_slot": {"bg-1": 2, "bg-2": 2},
                "shortfall_reason": "",
            },
            "active_slots": [],
            "current_proposals": [],
            "next_proposals": [],
            "selected_experiment": {
                "proposal_id": "market-forecast-v3-p1",
                "proposal_snapshot": {
                    "proposal_id": "market-forecast-v3-p1",
                    "title": "Tighten rolling validation",
                    "target_area": "validation",
                },
                "selection_rationale": "best fit",
                "sanity_attempts": 1,
                "design": {
                    "proposal_id": "market-forecast-v3-p1",
                    "target_area": "validation",
                    "primary_intervention": "Reduce leakage risk in evaluation",
                },
                "diagnosis_history": [],
                "analysis_summary": None,
            },
            "local_changeset": {
                "integration_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                "patch_artifacts": [
                    {
                        "producer_slot_id": "aux-1",
                        "purpose": "candidate patch bundle",
                        "patch_path": ".ml-metaopt/artifacts/patches/batch-20260405-0001/aux-1.patch",
                        "target_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                    }
                ],
                "apply_results": [
                    {
                        "patch_path": ".ml-metaopt/artifacts/patches/batch-20260405-0001/aux-1.patch",
                        "status": "applied",
                        "error": None,
                    }
                ],
                "verification_notes": ["pytest passed"],
                "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-20260405-0001.tar.gz",
                "data_manifest_uri": ".ml-metaopt/artifacts/data/batch-20260405-0001.json",
            },
            "remote_batches": [],
            "baseline": {
                "aggregate": 0.1284,
                "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320},
            },
            "completed_experiments": [
                {"batch_id": "batch-20260401-0001", "aggregate": 0.1292},
            ],
            "key_learnings": ["Leakage checks matter more than model capacity early in the campaign"],
            "no_improve_iterations": 1,
            "runtime_capabilities": {
                "verified_at": "2026-04-06T00:00:00Z",
                "available_skills": ["metaopt-analysis-worker", "metaopt-diagnosis-worker"],
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
        malformed_handoff: bool = False,
        executor_events: dict[str, dict] | None = None,
        worker_results: dict[str, dict] | None = None,
    ) -> tuple[dict, dict, Path]:
        load_handoff = self._write_load_handoff(tempdir, malformed=malformed_handoff)
        state_path = tempdir / ".ml-metaopt" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        tasks_dir = tempdir / ".ml-metaopt" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        worker_results_dir = tempdir / ".ml-metaopt" / "worker-results"
        worker_results_dir.mkdir(parents=True, exist_ok=True)
        executor_events_dir = tempdir / ".ml-metaopt" / "executor-events"
        executor_events_dir.mkdir(parents=True, exist_ok=True)
        output_path = tempdir / ".ml-metaopt" / "handoffs" / f"{mode}.latest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        for name, payload in (executor_events or {}).items():
            (executor_events_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        for name, payload in (worker_results or {}).items():
            (worker_results_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")

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
                "--executor-events-dir",
                str(executor_events_dir),
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

    def test_plan_remote_batch_emits_manifest_and_batch_id(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            batch_id = self._today_batch_id()
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="plan_remote_batch",
                state=self._base_state(),
            )

            self.assertEqual(payload["phase"], "PLAN_REMOTE_BATCH")
            self.assertEqual(payload["outcome"], "planned")
            self.assertEqual(payload["batch_id"], batch_id)
            self.assertEqual(payload["recommended_next_machine_state"], "ENQUEUE_REMOTE_BATCH")
            self.assertTrue(payload["manifest_path"].endswith(f"{batch_id}.json"))
            self.assertEqual(updated_state["machine_state"], "ENQUEUE_REMOTE_BATCH")

    def test_gate_remote_batch_records_enqueue_ack_and_advances_to_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            batch_id = self._today_batch_id()
            state = self._base_state()
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    f"enqueue-{batch_id}": {
                        "batch_id": batch_id,
                        "queue_ref": "ray-queue-123",
                        "status": "queued",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "waiting")
            self.assertEqual(updated_state["machine_state"], "WAIT_FOR_REMOTE_BATCH")
            self.assertEqual(updated_state["remote_batches"][0]["batch_id"], batch_id)
            self.assertEqual(updated_state["remote_batches"][0]["queue_ref"], "ray-queue-123")

    def test_gate_remote_batch_uses_pending_batch_id_from_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["pending_remote_batch"] = {
                "batch_id": "batch-20260406-0002",
                "manifest_path": ".ml-metaopt/artifacts/manifests/batch-20260406-0002.json",
            }
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "enqueue-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "queue_ref": "ray-queue-123",
                        "status": "queued",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "waiting")
            self.assertEqual(updated_state["machine_state"], "WAIT_FOR_REMOTE_BATCH")
            self.assertEqual(updated_state["remote_batches"][0]["batch_id"], "batch-20260406-0002")
            self.assertEqual(updated_state["remote_batches"][0]["queue_ref"], "ray-queue-123")

    def test_gate_remote_batch_requests_results_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "running"}]
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z", "started_at": "2026-04-06T10:02:00Z"},
                    }
                },
            )

            self.assertEqual(payload["outcome"], "fetch_results")
            self.assertEqual(updated_state["machine_state"], "WAIT_FOR_REMOTE_BATCH")
            self.assertEqual(updated_state["remote_batches"][0]["status"], "completed")

    def test_gate_remote_batch_emits_analysis_task_when_results_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "completed"}]
            payload, _, tasks_dir = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z", "started_at": "2026-04-06T10:02:00Z"},
                    },
                    "remote-results-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "best_aggregate_result": {"metric": "rmse", "value": 0.1213},
                        "per_dataset": {"ds_main": 0.1208, "ds_holdout": 0.1225},
                        "artifact_locations": {
                            "code": "s3://metaopt/artifacts/code/batch-20260406-0002.tar.gz",
                            "data_manifest": "s3://metaopt/artifacts/data/batch-20260406-0002.json",
                            "metrics": "s3://metaopt/results/batch-20260406-0002/metrics.json",
                        },
                        "logs_location": "s3://metaopt/results/batch-20260406-0002/logs.txt",
                    },
                },
            )

            self.assertEqual(payload["outcome"], "run_analysis")
            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-analysis-worker")
            task_file = tasks_dir / "remote-analysis-batch-20260406-0002.md"
            self.assertTrue(task_file.exists())
            task_text = task_file.read_text(encoding="utf-8")
            self.assertIn("metaopt-analysis-worker", task_text)
            self.assertIn("Objective Context", task_text)
            self.assertIn("Baseline Context", task_text)
            self.assertIn("Result Context", task_text)
            self.assertIn("Expected JSON fields", task_text)

    def test_analyze_remote_results_updates_state_and_advances(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "completed"}]
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="analyze_remote_results",
                state=state,
                executor_events={
                    "remote-results-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "best_aggregate_result": {"metric": "rmse", "value": 0.1213},
                        "per_dataset": {"ds_main": 0.1208, "ds_holdout": 0.1225},
                        "artifact_locations": {
                            "code": "s3://metaopt/artifacts/code/batch-20260406-0002.tar.gz",
                            "data_manifest": "s3://metaopt/artifacts/data/batch-20260406-0002.json",
                            "metrics": "s3://metaopt/results/batch-20260406-0002/metrics.json",
                        },
                        "logs_location": "s3://metaopt/results/batch-20260406-0002/logs.txt",
                    }
                },
                worker_results={
                    "remote-analysis-batch-20260406-0002": {
                        "judgment": "improvement",
                        "new_aggregate": 0.1213,
                        "delta": -0.0071,
                        "learnings": ["Rolling validation tightened the aggregate metric."],
                        "invalidations": [{"proposal_id": "market-forecast-v3-p9", "reason": "validation issue addressed"}],
                        "carry_over_candidates": [{"title": "Try stricter cutoff", "rationale": "Follow-on validation refinement"}],
                    }
                },
            )

            self.assertEqual(payload["outcome"], "analyzed")
            self.assertEqual(updated_state["machine_state"], "ROLL_ITERATION")
            self.assertEqual(updated_state["baseline"]["aggregate"], 0.1213)
            self.assertEqual(updated_state["no_improve_iterations"], 0)
            self.assertEqual(updated_state["completed_experiments"][-1]["batch_id"], "batch-20260406-0002")
            self.assertEqual(updated_state["selected_experiment"]["analysis_summary"]["judgment"], "improvement")

    def test_gate_remote_batch_routes_remote_config_failure_to_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "running"}]
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "failed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z"},
                        "classification": "config_error",
                        "message": "dataset path invalid",
                        "returncode": 2,
                    }
                },
                worker_results={
                    "remote-diagnosis-batch-20260406-0002": {
                        "root_cause": "dataset path invalid on cluster",
                        "classification": "config_error",
                        "fix_recommendation": {
                            "action": "adjust_config",
                            "code_guidance": None,
                            "config_guidance": "repair dataset path in campaign config",
                        },
                        "learnings": ["Remote execution is blocked by invalid dataset path configuration."],
                    }
                },
            )

            self.assertEqual(payload["outcome"], "blocked_config")
            self.assertEqual(updated_state["status"], "BLOCKED_CONFIG")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_CONFIG")

    def test_gate_remote_batch_emits_diagnosis_task_for_failed_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "running"}]
            payload, updated_state, tasks_dir = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "failed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z"},
                        "classification": "infra_error",
                        "message": "worker exited on cluster",
                        "returncode": 137,
                    }
                },
            )

            self.assertEqual(payload["outcome"], "run_remote_diagnosis")
            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-diagnosis-worker")
            self.assertEqual(updated_state["machine_state"], "WAIT_FOR_REMOTE_BATCH")
            task_text = (tasks_dir / "remote-diagnosis-batch-20260406-0002.md").read_text(encoding="utf-8")
            self.assertIn("metaopt-diagnosis-worker", task_text)
            self.assertIn("Failure Classification", task_text)
            self.assertIn("Result File", task_text)

    def test_malformed_load_handoff_returns_runtime_error_without_state_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            original = json.dumps(state, sort_keys=True)
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="plan_remote_batch",
                state=state,
                malformed_handoff=True,
            )

            self.assertEqual(payload["outcome"], "runtime_error")
            self.assertEqual(payload["recommended_next_action"], "repair or replace load_campaign.latest.json")
            self.assertEqual(json.dumps(updated_state, sort_keys=True), original)

    def _assert_envelope_keys(self, payload: dict, *, handoff_type: str = "REMOTE_EXECUTION_CONTROL", control_agent: str = "metaopt-remote-execution-control") -> None:
        self.assertEqual(payload["handoff_type"], handoff_type)
        self.assertEqual(payload["control_agent"], control_agent)
        self.assertIsInstance(payload["launch_requests"], list)
        self.assertIsInstance(payload["state_patch"], dict)
        self.assertIsInstance(payload["executor_directives"], list)
        self.assertIn("summary", payload)
        self.assertIn("warnings", payload)
        self.assertIn("recommended_next_machine_state", payload)

    def test_plan_remote_batch_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_remote_batch",
                state=self._base_state(),
            )
            self._assert_envelope_keys(payload)

    def test_analyze_remote_results_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "completed"}]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="analyze_remote_results",
                state=state,
                executor_events={
                    "remote-results-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "per_dataset": {"ds_main": 0.1208, "ds_holdout": 0.1225},
                    }
                },
                worker_results={
                    "remote-analysis-batch-20260406-0002": {
                        "judgment": "improvement",
                        "new_aggregate": 0.1213,
                        "delta": -0.0071,
                        "learnings": [],
                    }
                },
            )
            self._assert_envelope_keys(payload)

    def test_runtime_error_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_remote_batch",
                state=self._base_state(),
                malformed_handoff=True,
            )
            self._assert_envelope_keys(payload)
            self.assertEqual(payload["outcome"], "runtime_error")

    # ── directive-driven execution tests ─────────────────────────────

    def test_plan_remote_batch_emits_write_manifest_and_enqueue_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            batch_id = self._today_batch_id()
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_remote_batch",
                state=self._base_state(),
            )

            directives = payload["executor_directives"]
            actions = [d["action"] for d in directives]
            self.assertEqual(actions, ["write_manifest", "enqueue_batch"])

            write_d = directives[0]
            self.assertEqual(write_d["manifest_path"], payload["manifest_path"])
            self.assertEqual(write_d["batch_id"], batch_id)
            self.assertIn("reason", write_d)

            enqueue_d = directives[1]
            self.assertEqual(enqueue_d["command"], "python3 /opt/ray-hetzner/metaopt/enqueue_batch.py --manifest")
            self.assertEqual(enqueue_d["manifest_path"], payload["manifest_path"])
            self.assertIn("reason", enqueue_d)

    def test_gate_remote_batch_enqueue_ack_emits_poll_directive(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            batch_id = self._today_batch_id()
            state = self._base_state()
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    f"enqueue-{batch_id}": {
                        "batch_id": batch_id,
                        "queue_ref": "ray-queue-123",
                        "status": "queued",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "waiting")
            directives = payload["executor_directives"]
            self.assertEqual(len(directives), 1)
            self.assertEqual(directives[0]["action"], "poll_batch_status")
            self.assertEqual(directives[0]["command"], "python3 /opt/ray-hetzner/metaopt/get_batch_status.py --batch-id")
            self.assertEqual(directives[0]["batch_id"], batch_id)

    def test_gate_remote_batch_still_running_emits_poll_directive(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "running"}]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "running",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "waiting")
            directives = payload["executor_directives"]
            self.assertEqual(len(directives), 1)
            self.assertEqual(directives[0]["action"], "poll_batch_status")
            self.assertEqual(directives[0]["command"], "python3 /opt/ray-hetzner/metaopt/get_batch_status.py --batch-id")
            self.assertEqual(directives[0]["batch_id"], "batch-20260406-0002")

    def test_gate_remote_batch_completed_no_results_emits_fetch_directive(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "running"}]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z", "started_at": "2026-04-06T10:02:00Z"},
                    }
                },
            )

            self.assertEqual(payload["outcome"], "fetch_results")
            directives = payload["executor_directives"]
            self.assertEqual(len(directives), 1)
            self.assertEqual(directives[0]["action"], "fetch_batch_results")
            self.assertEqual(directives[0]["command"], "python3 /opt/ray-hetzner/metaopt/fetch_batch_results.py --batch-id")
            self.assertEqual(directives[0]["batch_id"], "batch-20260406-0002")

    def test_gate_remote_batch_analysis_launch_has_no_executor_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "completed"}]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z", "started_at": "2026-04-06T10:02:00Z"},
                    },
                    "remote-results-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "best_aggregate_result": {"metric": "rmse", "value": 0.1213},
                        "per_dataset": {"ds_main": 0.1208, "ds_holdout": 0.1225},
                        "artifact_locations": {},
                        "logs_location": "s3://logs",
                    },
                },
            )

            self.assertEqual(payload["outcome"], "run_analysis")
            self.assertEqual(payload["worker_ref"], "metaopt-analysis-worker")
            self.assertEqual(payload["executor_directives"], [])

    def test_gate_remote_batch_diagnosis_launch_has_no_executor_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "running"}]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "failed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z"},
                        "classification": "infra_error",
                        "message": "worker exited",
                        "returncode": 137,
                    }
                },
            )

            self.assertEqual(payload["outcome"], "run_remote_diagnosis")
            self.assertEqual(payload["worker_ref"], "metaopt-diagnosis-worker")
            self.assertEqual(payload["executor_directives"], [])

    def test_plan_remote_batch_stability_emits_same_directives(self) -> None:
        """Re-running plan on a state with pending_remote_batch uses same batch_id in directives."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["pending_remote_batch"] = {
                "batch_id": "batch-20260406-0002",
                "manifest_path": ".ml-metaopt/artifacts/manifests/batch-20260406-0002.json",
            }
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_remote_batch",
                state=state,
            )

            directives = payload["executor_directives"]
            self.assertEqual(directives[0]["batch_id"], "batch-20260406-0002")
            self.assertEqual(directives[1]["manifest_path"], ".ml-metaopt/artifacts/manifests/batch-20260406-0002.json")

    # ── Task 4: fail-closed guardrails ────────────────────────────────

    def test_analyze_blocks_to_blocked_protocol_without_analysis_artifact(self) -> None:
        """Semantic result judgment must block with BLOCKED_PROTOCOL if analysis artifact is missing."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "ANALYZE_RESULTS"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "completed"}]
            # Provide remote results but NO analysis artifact
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="analyze_remote_results",
                state=state,
                executor_events={
                    "remote-results-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "per_dataset": {"ds_main": 0.1208, "ds_holdout": 0.1225},
                    }
                },
            )

            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_PROTOCOL")

    def test_gate_analysis_launch_request_has_preferred_model_claude_opus(self) -> None:
        """Analysis worker launch must be a legal auxiliary launch with preferred_model."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "completed"}]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z"},
                    },
                    "remote-results-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "completed",
                        "per_dataset": {"ds_main": 0.1208, "ds_holdout": 0.1225},
                        "artifact_locations": {},
                        "logs_location": "s3://logs",
                    },
                },
            )

            self.assertEqual(payload["outcome"], "run_analysis")
            self.assertGreater(len(payload["launch_requests"]), 0)
            lr = payload["launch_requests"][0]
            self.assertEqual(lr["slot_class"], "auxiliary")
            self.assertEqual(lr["mode"], "analysis")
            self.assertEqual(lr["worker_ref"], "metaopt-analysis-worker")
            self.assertEqual(lr["model_class"], "strong_reasoner")
            self.assertEqual(lr["preferred_model"], "claude-opus-4.6-fast")

    def test_gate_remote_diagnosis_launch_request_has_preferred_model_claude_opus(self) -> None:
        """Remote diagnosis worker launch must be a legal auxiliary launch with preferred_model."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["remote_batches"] = [{"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "running"}]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_remote_batch",
                state=state,
                executor_events={
                    "remote-status-batch-20260406-0002": {
                        "batch_id": "batch-20260406-0002",
                        "status": "failed",
                        "timestamps": {"queued_at": "2026-04-06T10:00:00Z"},
                        "classification": "infra_error",
                        "message": "worker exited",
                        "returncode": 137,
                    }
                },
            )

            self.assertEqual(payload["outcome"], "run_remote_diagnosis")
            self.assertGreater(len(payload["launch_requests"]), 0)
            lr = payload["launch_requests"][0]
            self.assertEqual(lr["slot_class"], "auxiliary")
            self.assertEqual(lr["mode"], "diagnosis")
            self.assertEqual(lr["worker_ref"], "metaopt-diagnosis-worker")
            self.assertEqual(lr["model_class"], "strong_reasoner")
            self.assertEqual(lr["preferred_model"], "claude-opus-4.6-fast")

    def test_queue_only_no_raw_cluster_directives(self) -> None:
        """Remote execution must remain queue-only; all directives must use allowed actions."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            batch_id = self._today_batch_id()
            # Plan phase
            plan_payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_remote_batch",
                state=self._base_state(),
            )
            blocked_actions = {"ssh_command", "raw_ssh", "shell_exec", "kubectl_exec"}
            for d in plan_payload.get("executor_directives", []):
                self.assertNotIn(d["action"], blocked_actions,
                                 f"raw-cluster action {d['action']!r} found in plan_remote_batch")

    def test_agent_profile_exists_and_declares_all_modes(self) -> None:
        self.assertTrue(AGENT_PROFILE.exists(), f"missing {AGENT_PROFILE}")
        content = AGENT_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-remote-execution-control", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("plan_remote_batch", content)
        self.assertIn("gate_remote_batch", content)
        self.assertIn("analyze_remote_results", content)
        self.assertIn("scripts/remote_execution_control_handoff.py", content)

    def test_analysis_worker_profile_exists_and_is_leaf_only(self) -> None:
        self.assertTrue(ANALYSIS_WORKER_PROFILE.exists(), f"missing {ANALYSIS_WORKER_PROFILE}")
        content = ANALYSIS_WORKER_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-analysis-worker", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("Do not launch subagents.", content)
        self.assertIn("Do not mutate `.ml-metaopt/state.json`.", content)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local_execution_control_handoff.py"
AGENT_PROFILE = ROOT / ".github" / "agents" / "metaopt-local-execution-control.agent.md"
WORKER_PROFILE = ROOT / ".github" / "agents" / "metaopt-materialization-worker.agent.md"
DIAGNOSIS_WORKER_PROFILE = ROOT / ".github" / "agents" / "metaopt-diagnosis-worker.agent.md"


class LocalExecutionControlAgentTests(unittest.TestCase):
    def _write_load_handoff(self, tempdir: Path) -> Path:
        handoff = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "producer": "metaopt-load-campaign",
            "phase": "LOAD_CAMPAIGN",
            "outcome": "ok",
            "campaign_id": "market-forecast-v3",
            "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
            "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
            "artifacts": {
                "code_roots": ["."],
                "data_roots": ["data"],
                "exclude": [".git", ".venv", "logs", ".ml-metaopt"],
            },
            "execution": {
                "runner_type": "ray_queue_runner",
                "entrypoint": "python3 /srv/metaopt/project/scripts/ray_runner.py",
            },
            "sanity": {
                "command": "python3 scripts/local_sanity.py --fast",
                "max_duration_seconds": 60,
                "require_zero_temporal_leakage": True,
                "require_config_load": True,
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
            "machine_state": "MATERIALIZE_CHANGESET",
            "current_iteration": 1,
            "next_action": "materialize selected experiment",
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
                "sanity_attempts": 0,
                "design": {
                    "proposal_id": "market-forecast-v3-p1",
                    "target_area": "validation",
                    "primary_intervention": "Reduce leakage risk in evaluation",
                    "artifact_expectations": {
                        "code_artifact": "immutable packaged source tree",
                        "data_manifest": "manifest-linked dataset artifact summary",
                        "patch_artifact": "unified diff patch suitable for mechanical integration",
                    },
                },
                "diagnosis_history": [],
                "analysis_summary": None,
            },
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
                "available_skills": ["metaopt-materialization-worker", "metaopt-diagnosis-worker"],
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
        executor_events: dict[str, dict] | None = None,
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

    def test_plan_mode_emits_standard_materialization_task(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, tasks_dir = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=self._base_state(),
            )

            self.assertEqual(payload["phase"], "PLAN_LOCAL_CHANGESET")
            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-materialization-worker")
            self.assertEqual(payload["materialization_mode"], "standard")
            self.assertEqual(payload["recommended_next_machine_state"], "MATERIALIZE_CHANGESET")
            task_file = tasks_dir / "materialization-1.md"
            self.assertTrue(task_file.exists())
            task_text = task_file.read_text(encoding="utf-8")
            self.assertIn("standard", task_text)
            self.assertIn("metaopt-materialization-worker", task_text)
            self.assertIn("Required Worktree", task_text)
            self.assertIn("Primary Intervention", task_text)
            self.assertEqual(updated_state["machine_state"], "MATERIALIZE_CHANGESET")

    def test_plan_mode_emits_remediation_task_after_fix_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["selected_experiment"]["sanity_attempts"] = 1
            state["selected_experiment"]["diagnosis_history"] = [
                {
                    "attempt": 1,
                    "root_cause": "missing guard",
                    "classification": "code_error",
                    "action": "fix",
                    "code_guidance": "Add temporal leakage guard",
                    "config_guidance": None,
                    "diagnosed_at": "2026-04-06T00:10:00Z",
                }
            ]
            payload, _, tasks_dir = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=state,
                worker_results={
                    "diagnosis-1": {
                        "root_cause": "missing guard",
                        "classification": "code_error",
                        "fix_recommendation": {
                            "action": "fix",
                            "code_guidance": "Add temporal leakage guard",
                            "config_guidance": None,
                        },
                    }
                },
            )

            self.assertEqual(payload["materialization_mode"], "remediation")
            task_text = (tasks_dir / "materialization-2.md").read_text(encoding="utf-8")
            self.assertIn("remediation", task_text)
            self.assertIn("Diagnosis Guidance", task_text)
            self.assertIn("Current Local Changeset", task_text)

    def test_plan_mode_emits_conflict_resolution_task_after_apply_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            payload, _, tasks_dir = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=state,
                executor_events={
                    "local_changeset-1": {
                        "integration_worktree": ".ml-metaopt/worktrees/iter-1-integration",
                        "apply_results": [
                            {
                                "patch_path": ".ml-metaopt/artifacts/patches/batch-0001/aux-1.patch",
                                "status": "conflict",
                                "error": "merge conflict in src/pipeline.py",
                            }
                        ],
                        "code_artifact_uri": None,
                        "data_manifest_uri": None,
                    }
                },
            )

            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-materialization-worker")
            self.assertEqual(payload["materialization_mode"], "conflict_resolution")
            self.assertEqual(payload["required_worktree"], ".ml-metaopt/worktrees/iter-1-integration")
            task_text = (tasks_dir / "materialization-1.md").read_text(encoding="utf-8")
            self.assertIn("conflict_resolution", task_text)
            self.assertIn("Conflicting Apply Results", task_text)
            self.assertIn(".ml-metaopt/worktrees/iter-1-integration", task_text)

    def test_gate_mode_advances_to_enqueue_on_sanity_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                worker_results={
                    "materialization-1": {
                        "status": "completed",
                        "patch_artifacts": [
                            {
                                "producer_slot_id": "aux-1",
                                "purpose": "candidate patch bundle",
                                "patch_path": ".ml-metaopt/artifacts/patches/batch-0001/aux-1.patch",
                                "target_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                            }
                        ],
                        "verification_notes": ["pytest passed"],
                    }
                },
                executor_events={
                    "local_changeset-1": {
                        "integration_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                        "apply_results": [
                            {
                                "patch_path": ".ml-metaopt/artifacts/patches/batch-0001/aux-1.patch",
                                "status": "applied",
                                "error": None,
                            }
                        ],
                        "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-0001.tar.gz",
                        "data_manifest_uri": ".ml-metaopt/artifacts/data/batch-0001.json",
                    },
                    "sanity-1": {
                        "status": "passed",
                        "exit_code": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "duration_seconds": 4.2,
                    },
                },
            )

            self.assertEqual(payload["outcome"], "enqueue_remote_batch")
            self.assertEqual(payload["recommended_next_machine_state"], "ENQUEUE_REMOTE_BATCH")
            self.assertEqual(updated_state["machine_state"], "ENQUEUE_REMOTE_BATCH")
            self.assertEqual(updated_state["next_action"], "enqueue remote batch")
            self.assertIsInstance(updated_state["local_changeset"], dict)
            self.assertEqual(updated_state["local_changeset"]["code_artifact_uri"], ".ml-metaopt/artifacts/code/batch-0001.tar.gz")

    def test_gate_mode_requests_diagnosis_after_sanity_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, tasks_dir = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                executor_events={
                    "sanity-1": {
                        "status": "failed",
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "temporal leakage detected",
                        "duration_seconds": 2.3,
                    }
                },
            )

            self.assertEqual(payload["outcome"], "run_diagnosis")
            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-diagnosis-worker")
            self.assertEqual(payload["task_file"], ".ml-metaopt/tasks/diagnosis-1.md")
            self.assertEqual(payload["recommended_next_machine_state"], "LOCAL_SANITY")
            self.assertEqual(updated_state["machine_state"], "LOCAL_SANITY")
            self.assertEqual(updated_state["selected_experiment"]["sanity_attempts"], 0)
            task_text = (tasks_dir / "diagnosis-1.md").read_text(encoding="utf-8")
            self.assertIn("metaopt-diagnosis-worker", task_text)
            self.assertIn("Failure Type: `local_sanity`", task_text)
            self.assertIn("Sanity Config", task_text)
            self.assertIn("Result File", task_text)

    def test_gate_mode_routes_fix_diagnosis_back_to_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                executor_events={
                    "sanity-1": {
                        "status": "failed",
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "temporal leakage detected",
                        "duration_seconds": 2.3,
                    }
                },
                worker_results={
                    "diagnosis-1": {
                        "root_cause": "missing temporal split guard",
                        "classification": "code_error",
                        "fix_recommendation": {
                            "action": "fix",
                            "code_guidance": "Add strict cutoff before feature generation",
                            "config_guidance": None,
                        },
                    }
                },
            )

            self.assertEqual(payload["outcome"], "rematerialize")
            self.assertEqual(updated_state["machine_state"], "MATERIALIZE_CHANGESET")
            self.assertEqual(updated_state["selected_experiment"]["sanity_attempts"], 1)
            self.assertEqual(updated_state["selected_experiment"]["diagnosis_history"][0]["action"], "fix")

    def test_gate_mode_routes_adjust_config_to_blocked_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                executor_events={
                    "sanity-1": {
                        "status": "failed",
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "bad local path",
                        "duration_seconds": 1.0,
                    }
                },
                worker_results={
                    "diagnosis-1": {
                        "root_cause": "dataset path invalid",
                        "classification": "config_error",
                        "fix_recommendation": {
                            "action": "adjust_config",
                            "code_guidance": None,
                            "config_guidance": "repair dataset path in campaign config",
                        },
                    }
                },
            )

            self.assertEqual(payload["outcome"], "blocked_config")
            self.assertEqual(updated_state["status"], "BLOCKED_CONFIG")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_CONFIG")

    def test_gate_mode_attempt_cap_routes_to_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["selected_experiment"]["sanity_attempts"] = 3
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=state,
                executor_events={
                    "sanity-4": {
                        "status": "failed",
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "still broken",
                        "duration_seconds": 1.0,
                    }
                },
            )

            self.assertEqual(payload["outcome"], "failed")
            self.assertEqual(updated_state["status"], "FAILED")
            self.assertEqual(updated_state["machine_state"], "FAILED")

    def _assert_envelope_keys(self, payload: dict, *, handoff_type: str = "LOCAL_EXECUTION_CONTROL", control_agent: str = "metaopt-local-execution-control") -> None:
        self.assertEqual(payload["handoff_type"], handoff_type)
        self.assertEqual(payload["control_agent"], control_agent)
        self.assertIsInstance(payload["launch_requests"], list)
        self.assertIsInstance(payload["state_patch"], dict)
        self.assertIsInstance(payload["executor_directives"], list)
        self.assertIn("summary", payload)
        self.assertIn("warnings", payload)
        self.assertIn("recommended_next_machine_state", payload)

    def test_plan_mode_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=self._base_state(),
            )
            self._assert_envelope_keys(payload)

    def test_gate_sanity_pass_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                worker_results={
                    "materialization-1": {
                        "status": "completed",
                        "patch_artifacts": [],
                        "verification_notes": ["ok"],
                    }
                },
                executor_events={
                    "local_changeset-1": {
                        "integration_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                        "apply_results": [],
                        "code_artifact_uri": "code.tar.gz",
                        "data_manifest_uri": "data.json",
                    },
                    "sanity-1": {
                        "status": "passed",
                        "exit_code": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "duration_seconds": 1,
                    },
                },
            )
            self._assert_envelope_keys(payload)

    # ── executor_directives tests ──────────────────────────────────────

    def test_plan_standard_emits_executor_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=self._base_state(),
            )
            directives = payload["executor_directives"]
            actions = [d["action"] for d in directives]
            self.assertEqual(
                actions,
                ["apply_patch_artifacts", "package_code_artifact", "package_data_manifest", "run_sanity"],
            )
            for d in directives:
                self.assertIsInstance(d["reason"], str)
                self.assertTrue(d["reason"])

    def test_plan_standard_directives_carry_context(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=self._base_state(),
            )
            directives = payload["executor_directives"]
            by_action = {d["action"]: d for d in directives}
            self.assertIn("result_file", by_action["apply_patch_artifacts"])
            self.assertIn("target_worktree", by_action["apply_patch_artifacts"])
            self.assertIn("worktree", by_action["package_code_artifact"])
            self.assertIn("code_roots", by_action["package_code_artifact"])
            self.assertIn("worktree", by_action["package_data_manifest"])
            self.assertIn("data_roots", by_action["package_data_manifest"])
            self.assertIn("worktree", by_action["run_sanity"])
            self.assertIn("command", by_action["run_sanity"])
            self.assertIn("max_duration_seconds", by_action["run_sanity"])

    def test_plan_remediation_has_no_executor_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["selected_experiment"]["sanity_attempts"] = 1
            state["selected_experiment"]["diagnosis_history"] = [
                {
                    "attempt": 1,
                    "root_cause": "missing guard",
                    "classification": "code_error",
                    "action": "fix",
                    "code_guidance": "Add temporal leakage guard",
                    "config_guidance": None,
                    "diagnosed_at": "2026-04-06T00:10:00Z",
                }
            ]
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=state,
                worker_results={
                    "diagnosis-1": {
                        "root_cause": "missing guard",
                        "classification": "code_error",
                        "fix_recommendation": {
                            "action": "fix",
                            "code_guidance": "Add temporal leakage guard",
                            "config_guidance": None,
                        },
                    }
                },
            )
            self.assertEqual(payload["executor_directives"], [])

    def test_plan_conflict_resolution_has_no_executor_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=self._base_state(),
                executor_events={
                    "local_changeset-1": {
                        "integration_worktree": ".ml-metaopt/worktrees/iter-1-integration",
                        "apply_results": [
                            {
                                "patch_path": ".ml-metaopt/artifacts/patches/batch-0001/aux-1.patch",
                                "status": "conflict",
                                "error": "merge conflict in src/pipeline.py",
                            }
                        ],
                        "code_artifact_uri": None,
                        "data_manifest_uri": None,
                    }
                },
            )
            self.assertEqual(payload["executor_directives"], [])

    def test_gate_diagnosis_launch_has_no_executor_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                executor_events={
                    "sanity-1": {
                        "status": "failed",
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "temporal leakage detected",
                        "duration_seconds": 2.3,
                    }
                },
            )
            self.assertEqual(payload["executor_directives"], [])

    def test_gate_sanity_pass_has_no_executor_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                worker_results={
                    "materialization-1": {
                        "status": "completed",
                        "patch_artifacts": [],
                        "verification_notes": ["ok"],
                    }
                },
                executor_events={
                    "local_changeset-1": {
                        "integration_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                        "apply_results": [],
                        "code_artifact_uri": "code.tar.gz",
                        "data_manifest_uri": "data.json",
                    },
                    "sanity-1": {
                        "status": "passed",
                        "exit_code": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "duration_seconds": 1,
                    },
                },
            )
            self.assertEqual(payload["executor_directives"], [])

    # ── Task 4: fail-closed guardrails ────────────────────────────────

    def test_plan_remediation_blocks_to_blocked_protocol_without_diagnosis_artifact(self) -> None:
        """Remediation path must block with BLOCKED_PROTOCOL if the diagnosis artifact is missing."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["selected_experiment"]["sanity_attempts"] = 1
            state["selected_experiment"]["diagnosis_history"] = [
                {
                    "attempt": 1,
                    "root_cause": "missing guard",
                    "classification": "code_error",
                    "action": "fix",
                    "code_guidance": "Add temporal leakage guard",
                    "config_guidance": None,
                    "diagnosed_at": "2026-04-06T00:10:00Z",
                }
            ]
            # NO diagnosis-1.json in worker_results → should block
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="plan_local_changeset",
                state=state,
            )

            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_PROTOCOL")

    def test_gate_diagnosis_launch_request_has_preferred_model_claude_opus(self) -> None:
        """Diagnosis worker launch must be a legal auxiliary launch with preferred_model."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_local_sanity",
                state=self._base_state(),
                executor_events={
                    "sanity-1": {
                        "status": "failed",
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "temporal leakage detected",
                        "duration_seconds": 2.3,
                    }
                },
            )

            self.assertEqual(payload["outcome"], "run_diagnosis")
            self.assertGreater(len(payload["launch_requests"]), 0)
            lr = payload["launch_requests"][0]
            self.assertEqual(lr["slot_class"], "auxiliary")
            self.assertEqual(lr["mode"], "diagnosis")
            self.assertEqual(lr["worker_ref"], "metaopt-diagnosis-worker")
            self.assertEqual(lr["model_class"], "strong_reasoner")
            self.assertEqual(lr["preferred_model"], "claude-opus-4.6-fast")

    def test_agent_profile_exists_and_declares_both_modes(self) -> None:
        self.assertTrue(AGENT_PROFILE.exists(), f"missing {AGENT_PROFILE}")
        content = AGENT_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-local-execution-control", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("plan_local_changeset", content)
        self.assertIn("gate_local_sanity", content)
        self.assertIn("scripts/local_execution_control_handoff.py", content)

    def test_materialization_worker_profile_exists_and_is_leaf_only(self) -> None:
        self.assertTrue(WORKER_PROFILE.exists(), f"missing {WORKER_PROFILE}")
        content = WORKER_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-materialization-worker", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("Do not launch subagents.", content)
        self.assertIn("Do not mutate `.ml-metaopt/state.json`.", content)

    def test_diagnosis_worker_profile_exists_and_is_leaf_only(self) -> None:
        self.assertTrue(DIAGNOSIS_WORKER_PROFILE.exists(), f"missing {DIAGNOSIS_WORKER_PROFILE}")
        content = DIAGNOSIS_WORKER_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-diagnosis-worker", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("Do not launch subagents.", content)
        self.assertIn("Do not mutate `.ml-metaopt/state.json`.", content)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "select_and_design_handoff.py"
CONTROL_AGENT = ROOT / ".github" / "agents" / "metaopt-select-design.agent.md"
SELECTION_AGENT = ROOT / ".github" / "agents" / "metaopt-selection-worker.agent.md"
DESIGN_AGENT = ROOT / ".github" / "agents" / "metaopt-design-worker.agent.md"


class SelectDesignAgentTests(unittest.TestCase):
    def _write_load_handoff(self, tempdir: Path, *, malformed: bool = False) -> Path:
        handoff = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        if malformed:
            handoff.write_text("{not-json", encoding="utf-8")
            return handoff
        payload = {
            "schema_version": 1,
            "handoff_type": "load_campaign.validate",
            "control_agent": "metaopt-load-campaign",
            "campaign_id": "market-forecast-v3",
            "campaign_valid": True,
            "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c",
            "runtime_config_hash": "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6",
            "goal": "Reduce forecasting error without destabilizing rollout assumptions",
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
            "datasets": [
                {
                    "id": "ds_main",
                    "local_path": "data/main.parquet",
                    "role": "train_eval",
                    "fingerprint": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                },
                {
                    "id": "ds_holdout",
                    "local_path": "data/holdout.parquet",
                    "role": "eval_only",
                    "fingerprint": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                },
            ],
            "execution": {
                "runner_type": "ray_queue_runner",
                "entrypoint": "python3 /srv/metaopt/project/scripts/ray_runner.py",
                "trial_budget": {"kind": "fixed_trials", "value": 128},
                "search_strategy": {"kind": "optuna_tpe", "seed": 1337},
            },
            "remote_queue": {
                "backend": "ray-hetzner",
                "retry_policy": {"max_attempts": 2},
            },
            "recommended_next_machine_state": "HYDRATE_STATE",
            "state_patch": None,
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
            "machine_state": "SELECT_EXPERIMENT",
            "current_iteration": 1,
            "next_action": "select experiment",
            "objective_snapshot": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                "improvement_threshold": 0.0005,
            },
            "proposal_cycle": {
                "cycle_id": "iter-1-cycle-1",
                "current_pool_frozen": False,
                "ideation_rounds_by_slot": {"bg-1": 2, "bg-2": 2},
                "shortfall_reason": "",
            },
            "active_slots": [],
            "current_proposals": [
                {
                    "proposal_id": "market-forecast-v3-p1",
                    "source_slot_id": "bg-1",
                    "creation_iteration": 1,
                    "created_at": "2026-04-06T00:00:00Z",
                    "title": "Tighten rolling validation",
                    "rationale": "Reduce leakage risk in evaluation",
                    "expected_impact": {"direction": "improve", "magnitude": "medium"},
                    "target_area": "validation",
                },
                {
                    "proposal_id": "market-forecast-v3-p2",
                    "source_slot_id": "bg-2",
                    "creation_iteration": 1,
                    "created_at": "2026-04-06T00:05:00Z",
                    "title": "Add lag feature family",
                    "rationale": "Improve temporal signal extraction",
                    "expected_impact": {"direction": "improve", "magnitude": "small"},
                    "target_area": "features",
                },
            ],
            "next_proposals": [],
            "selected_experiment": None,
            "local_changeset": None,
            "remote_batches": [],
            "baseline": {
                "aggregate": 0.1284,
                "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320},
            },
            "completed_experiments": [
                {"batch_id": "batch-20260401-0001", "aggregate": 0.1292},
            ],
            "key_learnings": [
                "Leakage checks matter more than model capacity early in the campaign",
            ],
            "no_improve_iterations": 1,
            "runtime_capabilities": {
                "verified_at": "2026-04-06T00:00:00Z",
                "available_skills": ["metaopt-selection-worker", "metaopt-design-worker"],
                "missing_skills": [],
                "degraded_lanes": [],
            },
        }

    def _run(
        self,
        tempdir: Path,
        *,
        mode: str,
        state: dict | str,
        malformed_handoff: bool = False,
    ) -> tuple[dict, Path, Path, Path]:
        handoff = self._write_load_handoff(tempdir, malformed=malformed_handoff)
        state_path = tempdir / ".ml-metaopt" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(state, str):
            state_path.write_text(state, encoding="utf-8")
        else:
            state_path.write_text(json.dumps(state), encoding="utf-8")
        tasks_dir = tempdir / ".ml-metaopt" / "tasks"
        results_dir = tempdir / ".ml-metaopt" / "worker-results"
        output_path = tempdir / ".ml-metaopt" / "handoffs" / "select_and_design.latest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        completed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--mode",
                mode,
                "--load-handoff",
                str(handoff),
                "--state-path",
                str(state_path),
                "--tasks-dir",
                str(tasks_dir),
                "--worker-results-dir",
                str(results_dir),
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
        return payload, state_path, tasks_dir, results_dir

    def test_plan_select_experiment_emits_selection_worker_handoff_and_freezes_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, state_path, tasks_dir, _ = self._run(
                Path(tempdir_str),
                mode="plan_select_experiment",
                state=self._base_state(),
            )

            self.assertEqual(payload["handoff_type"], "select_design.plan_select_experiment")
            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-selection-worker")
            self.assertEqual(payload["launch_requests"][0]["mode"], "selection")
            self.assertEqual(payload["recommended_next_machine_state"], "SELECT_EXPERIMENT")
            self.assertEqual(payload["task_file"], ".ml-metaopt/tasks/select-experiment-iter-1.md")
            self.assertEqual(payload["result_file"], ".ml-metaopt/worker-results/select-experiment-iter-1.json")

            task_text = (tasks_dir / "select-experiment-iter-1.md").read_text(encoding="utf-8")
            self.assertIn("metaopt-selection-worker", task_text)
            self.assertIn("Reduce forecasting error", task_text)
            self.assertIn("Frozen Current Proposals", task_text)
            self.assertIn("Proposal Policy", task_text)
            self.assertIn("ranking_rationale", task_text)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["proposal_cycle"]["current_pool_frozen"])
            self.assertEqual(state["machine_state"], "SELECT_EXPERIMENT")
            self.assertEqual(state["next_action"], "run selection worker")

    def test_plan_select_experiment_re_emits_same_task_when_selection_result_is_still_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            first_payload, state_path, tasks_dir, _ = self._run(
                tempdir,
                mode="plan_select_experiment",
                state=self._base_state(),
            )
            second_payload, _, _, _ = self._run(
                tempdir,
                mode="plan_select_experiment",
                state=json.loads(state_path.read_text(encoding="utf-8")),
            )

            self.assertEqual(first_payload["task_file"], second_payload["task_file"])
            self.assertEqual(first_payload["result_file"], second_payload["result_file"])
            self.assertTrue((tasks_dir / "select-experiment-iter-1.md").exists())

    def test_gate_selection_writes_partial_selected_experiment_and_plans_design(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            initial_state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=initial_state)
            selection_result = {
                "winning_proposal": initial_state["current_proposals"][0],
                "ranking_rationale": "Validation work is the highest-confidence improvement path.",
                "ranked_candidates": [
                    {"proposal_id": "market-forecast-v3-p1", "rank": 1},
                    {"proposal_id": "market-forecast-v3-p2", "rank": 2},
                ],
            }
            result_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(selection_result), encoding="utf-8")

            payload, state_path, tasks_dir, _ = self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )

            self.assertEqual(payload["handoff_type"], "select_design.gate_select_and_plan_design")
            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-design-worker")
            self.assertEqual(payload["proposal_id"], "market-forecast-v3-p1")
            self.assertEqual(payload["launch_requests"][0]["mode"], "design")
            self.assertEqual(payload["recommended_next_machine_state"], "DESIGN_EXPERIMENT")

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["machine_state"], "DESIGN_EXPERIMENT")
            self.assertEqual(state["next_action"], "run design worker")
            self.assertEqual(state["selected_experiment"]["proposal_id"], "market-forecast-v3-p1")
            self.assertEqual(state["selected_experiment"]["proposal_snapshot"]["proposal_id"], "market-forecast-v3-p1")
            self.assertIsNone(state["selected_experiment"]["design"])
            self.assertEqual(state["selected_experiment"]["diagnosis_history"], [])
            self.assertIsNone(state["selected_experiment"]["analysis_summary"])
            self.assertEqual(payload["state_patch"]["next_action"], "run design worker")
            self.assertEqual(payload["state_patch"]["selected_experiment"]["proposal_id"], "market-forecast-v3-p1")

            design_task = (tasks_dir / "design-experiment-iter-1.md").read_text(encoding="utf-8")
            self.assertIn("metaopt-design-worker", design_task)
            self.assertIn("Winning Proposal", design_task)
            self.assertIn("Execution Inputs", design_task)
            self.assertIn("artifact_expectations", design_task)

    def test_finalize_design_writes_design_and_advances_to_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            initial_state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=initial_state)
            selection_result = {
                "winning_proposal": initial_state["current_proposals"][0],
                "ranking_rationale": "Validation work is the highest-confidence improvement path.",
            }
            selection_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            selection_path.parent.mkdir(parents=True, exist_ok=True)
            selection_path.write_text(json.dumps(selection_result), encoding="utf-8")
            self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )
            design_result = {
                "proposal_id": "market-forecast-v3-p1",
                "experiment_name": "tighten-rolling-validation-v1",
                "description": "Tighten validation windows and leakage checks before feature changes.",
                "code_changes": [{"path": "src/train.py", "intent": "strengthen validation split rules"}],
                "search_space": {"validation_gap_days": [1, 3, 5]},
                "dataset_plan": [{"dataset_id": "ds_main", "role": "train_eval"}],
                "artifact_expectations": ["updated validation metrics", "leakage audit logs"],
                "success_criteria": {"metric": "rmse", "target": 0.1279},
                "execution_assumptions": {"runner": "ray_queue_runner"},
                "risks": ["slower iteration cadence"],
            }
            design_path = tempdir / ".ml-metaopt" / "worker-results" / "design-experiment-iter-1.json"
            design_path.write_text(json.dumps(design_result), encoding="utf-8")

            payload, state_path, _, _ = self._run(
                tempdir,
                mode="finalize_select_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )

            self.assertEqual(payload["handoff_type"], "select_design.finalize_select_design")
            self.assertEqual(payload["recommended_next_machine_state"], "MATERIALIZE_CHANGESET")
            self.assertEqual(payload["proposal_id"], "market-forecast-v3-p1")
            self.assertIn("validation", payload["design_summary"])

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["machine_state"], "MATERIALIZE_CHANGESET")
            self.assertEqual(state["next_action"], "materialize selected experiment")
            self.assertIsInstance(state["selected_experiment"]["design"], dict)
            self.assertEqual(state["selected_experiment"]["design"]["proposal_id"], "market-forecast-v3-p1")

    def test_gate_selection_rejects_unknown_winner_without_state_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=state)
            bad_selection_result = {
                "winning_proposal": {
                    "proposal_id": "missing",
                    "title": "Unknown proposal",
                },
                "ranking_rationale": "This should fail validation.",
            }
            selection_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            selection_path.parent.mkdir(parents=True, exist_ok=True)
            selection_path.write_text(json.dumps(bad_selection_result), encoding="utf-8")

            payload, state_path, _, _ = self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )

            self.assertEqual(payload["summary"], "winning proposal does not match frozen current_proposals")
            self.assertEqual(payload["recovery_action"], "repair selection worker result and re-run gating")
            self.assertIsNone(payload["recommended_next_machine_state"])
            state_after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIsNone(state_after["selected_experiment"])
            self.assertEqual(state_after["machine_state"], "SELECT_EXPERIMENT")

    def test_gate_selection_ignores_stale_previous_iteration_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            state = self._base_state()
            state["current_iteration"] = 2
            self._run(tempdir, mode="plan_select_experiment", state=state)
            stale_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_text(
                json.dumps(
                    {
                        "winning_proposal": state["current_proposals"][0],
                        "ranking_rationale": "stale result from prior iteration",
                    }
                ),
                encoding="utf-8",
            )

            payload, state_path, _, _ = self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )

            self.assertEqual(payload["summary"], "selection result missing")
            self.assertEqual(payload["recovery_action"], "stage selection worker result before gating")
            self.assertIsNone(payload["recommended_next_machine_state"])
            state_after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIsNone(state_after["selected_experiment"])

    def test_finalize_rejects_mismatched_design_without_state_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=state)
            selection_result = {
                "winning_proposal": state["current_proposals"][0],
                "ranking_rationale": "Validation work is the highest-confidence improvement path.",
            }
            selection_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            selection_path.parent.mkdir(parents=True, exist_ok=True)
            selection_path.write_text(json.dumps(selection_result), encoding="utf-8")
            self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )
            bad_design_result = {
                "proposal_id": "market-forecast-v3-p2",
                "experiment_name": "wrong-proposal",
            }
            design_path = tempdir / ".ml-metaopt" / "worker-results" / "design-experiment-iter-1.json"
            design_path.write_text(json.dumps(bad_design_result), encoding="utf-8")

            payload, state_path, _, _ = self._run(
                tempdir,
                mode="finalize_select_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )

            self.assertEqual(payload["summary"], "design result proposal_id does not match selected_experiment")
            self.assertEqual(payload["recovery_action"], "repair design worker result and re-run finalization")
            self.assertIsNone(payload["recommended_next_machine_state"])
            state_after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIsNone(state_after["selected_experiment"]["design"])
            self.assertEqual(state_after["machine_state"], "DESIGN_EXPERIMENT")

    def test_malformed_load_handoff_returns_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_select_experiment",
                state=self._base_state(),
                malformed_handoff=True,
            )

            self.assertEqual(payload["summary"], "load handoff unreadable")
            self.assertEqual(payload["recovery_action"], "repair or replace load_campaign.latest.json")
            self.assertIsNone(payload["recommended_next_machine_state"])

    def _assert_envelope_keys(self, payload: dict, *, handoff_type: str, control_agent: str = "metaopt-select-design") -> None:
        self.assertEqual(payload["handoff_type"], handoff_type)
        self.assertEqual(payload["control_agent"], control_agent)
        self.assertIsInstance(payload["launch_requests"], list)
        self.assertTrue(payload["state_patch"] is None or isinstance(payload["state_patch"], dict))
        self.assertIsInstance(payload["executor_directives"], list)
        self.assertIn("summary", payload)
        self.assertIn("warnings", payload)
        self.assertIn("recommended_next_machine_state", payload)

    def test_plan_select_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_select_experiment",
                state=self._base_state(),
            )
            self._assert_envelope_keys(payload, handoff_type="select_design.plan_select_experiment")

    def test_gate_select_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            initial_state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=initial_state)
            selection_result = {
                "winning_proposal": initial_state["current_proposals"][0],
                "ranking_rationale": "Best fit.",
            }
            result_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(selection_result), encoding="utf-8")
            payload, _, _, _ = self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )
            self._assert_envelope_keys(payload, handoff_type="select_design.gate_select_and_plan_design")

    def test_finalize_design_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            initial_state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=initial_state)
            selection_result = {
                "winning_proposal": initial_state["current_proposals"][0],
                "ranking_rationale": "Best fit.",
            }
            sel_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            sel_path.parent.mkdir(parents=True, exist_ok=True)
            sel_path.write_text(json.dumps(selection_result), encoding="utf-8")
            self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )
            design_result = {
                "proposal_id": "market-forecast-v3-p1",
                "experiment_name": "tighten-rolling-validation-v1",
                "description": "Tighten validation windows.",
            }
            design_path = tempdir / ".ml-metaopt" / "worker-results" / "design-experiment-iter-1.json"
            design_path.write_text(json.dumps(design_result), encoding="utf-8")
            payload, _, _, _ = self._run(
                tempdir,
                mode="finalize_select_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )
            self._assert_envelope_keys(payload, handoff_type="select_design.finalize_select_design")

    def test_runtime_error_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_select_experiment",
                state=self._base_state(),
                malformed_handoff=True,
            )
            self._assert_envelope_keys(payload, handoff_type="select_design.plan_select_experiment")
            self.assertEqual(payload["summary"], "load handoff unreadable")

    def test_plan_select_launch_request_includes_preferred_model_for_strong_reasoner(self) -> None:
        """Selection launch request must carry preferred_model == 'claude-opus-4.6-fast'."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_select_experiment",
                state=self._base_state(),
            )

            self.assertGreater(len(payload["launch_requests"]), 0)
            sel_lr = payload["launch_requests"][0]
            self.assertEqual(sel_lr["slot_class"], "auxiliary")
            self.assertEqual(sel_lr["mode"], "selection")
            self.assertEqual(sel_lr["worker_ref"], "metaopt-selection-worker")
            self.assertEqual(sel_lr["model_class"], "strong_reasoner")
            self.assertEqual(sel_lr["preferred_model"], "claude-opus-4.6-fast")

    def test_gate_select_design_launch_request_includes_preferred_model_for_strong_reasoner(self) -> None:
        """Design launch request must carry preferred_model == 'claude-opus-4.6-fast'."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            initial_state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=initial_state)
            selection_result = {
                "winning_proposal": initial_state["current_proposals"][0],
                "ranking_rationale": "Best fit.",
            }
            result_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(selection_result), encoding="utf-8")
            payload, _, _, _ = self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )

            self.assertGreater(len(payload["launch_requests"]), 0)
            design_lr = payload["launch_requests"][0]
            self.assertEqual(design_lr["slot_class"], "auxiliary")
            self.assertEqual(design_lr["mode"], "design")
            self.assertEqual(design_lr["worker_ref"], "metaopt-design-worker")
            self.assertEqual(design_lr["model_class"], "strong_reasoner")
            self.assertEqual(design_lr["preferred_model"], "claude-opus-4.6-fast")

    def test_finalize_rejects_design_with_materialization_lane_fields(self) -> None:
        """Design result containing patch_artifacts or apply_results must block to BLOCKED_PROTOCOL."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            state = self._base_state()
            self._run(tempdir, mode="plan_select_experiment", state=state)
            selection_result = {
                "winning_proposal": state["current_proposals"][0],
                "ranking_rationale": "Validation work is the highest-confidence improvement path.",
            }
            selection_path = tempdir / ".ml-metaopt" / "worker-results" / "select-experiment-iter-1.json"
            selection_path.parent.mkdir(parents=True, exist_ok=True)
            selection_path.write_text(json.dumps(selection_result), encoding="utf-8")
            self._run(
                tempdir,
                mode="gate_select_and_plan_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )
            # Design result that has crossed into materialization semantics
            bad_design_result = {
                "proposal_id": "market-forecast-v3-p1",
                "experiment_name": "tighten-rolling-validation-v1",
                "description": "Tighten validation windows.",
                "code_changes": [{"path": "src/train.py", "intent": "strengthen validation"}],
                "patch_artifacts": [{"file": "src/train.py", "diff": "--- a\n+++ b"}],
                "apply_results": [{"status": "applied", "file": "src/train.py"}],
            }
            design_path = tempdir / ".ml-metaopt" / "worker-results" / "design-experiment-iter-1.json"
            design_path.write_text(json.dumps(bad_design_result), encoding="utf-8")

            payload, state_path, _, _ = self._run(
                tempdir,
                mode="finalize_select_design",
                state=json.loads((tempdir / ".ml-metaopt" / "state.json").read_text(encoding="utf-8")),
            )

            # Must transition to BLOCKED_PROTOCOL, not runtime_error
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_PROTOCOL")
            self.assertIn("materialization", payload["summary"])
            self.assertIn("protocol violation", payload["state_patch"]["next_action"])
            self.assertTrue(len(payload["warnings"]) > 0)
            # State on disk must reflect BLOCKED_PROTOCOL
            state_after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state_after["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(state_after["machine_state"], "BLOCKED_PROTOCOL")
            self.assertIn("protocol violation", state_after["next_action"])
            self.assertIsNone(state_after["selected_experiment"]["design"])

    def test_agent_profiles_exist_and_are_programmatic_only(self) -> None:
        for profile in (CONTROL_AGENT, SELECTION_AGENT, DESIGN_AGENT):
            self.assertTrue(profile.exists(), f"missing {profile}")
            content = profile.read_text(encoding="utf-8")
            self.assertIn("model: gpt-5.4", content)
            self.assertIn("user-invocable: false", content)
        control_content = CONTROL_AGENT.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-select-design", control_content)
        self.assertIn("--mode plan_select_experiment", control_content)
        self.assertIn("--mode gate_select_and_plan_design", control_content)
        self.assertIn("--mode finalize_select_design", control_content)
        self.assertIn("name: metaopt-selection-worker", SELECTION_AGENT.read_text(encoding="utf-8"))
        self.assertIn("name: metaopt-design-worker", DESIGN_AGENT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

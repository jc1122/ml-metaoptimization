from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "iteration_close_control_handoff.py"
AGENT_PROFILE = ROOT / ".github" / "agents" / "metaopt-iteration-close-control.agent.md"
WORKER_PROFILE = ROOT / ".github" / "agents" / "metaopt-rollover-worker.agent.md"


class IterationCloseControlAgentTests(unittest.TestCase):
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
            "proposal_policy": {
                "current_target": 8,
                "current_floor": 4,
                "next_cap": 200,
                "distinctness_rule": "non_overlapping",
            },
            "stop_conditions": {
                "max_iterations": 20,
                "max_no_improve_iterations": 4,
                "target_metric": 0.1200,
                "max_wallclock_hours": 72,
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
            "machine_state": "ROLL_ITERATION",
            "current_iteration": 3,
            "next_action": "roll iteration",
            "objective_snapshot": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                "improvement_threshold": 0.0005,
            },
            "proposal_cycle": {
                "cycle_id": "iter-3-cycle-1",
                "current_pool_frozen": True,
                "ideation_rounds_by_slot": {"bg-1": 2, "bg-2": 2},
                "shortfall_reason": "",
            },
            "active_slots": [
                {
                    "slot_id": "bg-1",
                    "slot_class": "background",
                    "mode": "maintenance",
                    "model_class": "general_worker",
                    "requested_model": "Auto",
                    "resolved_model": "Auto",
                    "status": "running",
                    "attempt": 1,
                    "task_summary": "Read-only maintenance audit while quiescing",
                },
                {
                    "slot_id": "bg-2",
                    "slot_class": "background",
                    "mode": "maintenance",
                    "model_class": "general_worker",
                    "requested_model": "Auto",
                    "resolved_model": "Auto",
                    "status": "running",
                    "attempt": 1,
                    "task_summary": "Second maintenance lane",
                },
            ],
            "current_proposals": [],
            "next_proposals": [
                {
                    "proposal_id": "market-forecast-v3-p17",
                    "source_slot_id": "bg-1",
                    "creation_iteration": 3,
                    "created_at": "2026-04-06T09:00:00Z",
                    "title": "Try stricter validation cutoff",
                    "rationale": "Reduce temporal leakage margin",
                    "expected_impact": {"direction": "improve", "magnitude": "small"},
                    "target_area": "validation",
                }
            ],
            "selected_experiment": {
                "proposal_id": "market-forecast-v3-p16",
                "proposal_snapshot": {"proposal_id": "market-forecast-v3-p16", "title": "Tighten rolling validation"},
                "selection_rationale": "best fit",
                "sanity_attempts": 1,
                "design": {"proposal_id": "market-forecast-v3-p16"},
                "diagnosis_history": [],
                "analysis_summary": {
                    "judgment": "improvement",
                    "new_aggregate": 0.1213,
                    "delta": -0.0071,
                    "learnings": ["Rolling validation tightened the aggregate metric."],
                    "invalidations": [{"proposal_id": "market-forecast-v3-p9", "reason": "validation issue addressed"}],
                    "carry_over_candidates": [{"title": "Try stricter cutoff", "rationale": "Follow-on validation refinement"}],
                },
            },
            "local_changeset": {
                "integration_worktree": ".ml-metaopt/worktrees/iter-3-materialization",
                "patch_artifacts": [
                    {
                        "producer_slot_id": "bg-1",
                        "purpose": "maintenance patch bundle",
                        "patch_path": ".ml-metaopt/artifacts/patches/batch-20260406-0002/bg-1.patch",
                        "target_worktree": ".ml-metaopt/worktrees/iter-3-materialization",
                    }
                ],
                "apply_results": [
                    {
                        "patch_path": ".ml-metaopt/artifacts/patches/batch-20260406-0002/bg-1.patch",
                        "status": "applied",
                        "error": None,
                    }
                ],
                "verification_notes": ["pytest passed"],
                "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-20260406-0002.tar.gz",
                "data_manifest_uri": ".ml-metaopt/artifacts/data/batch-20260406-0002.json",
            },
            "remote_batches": [
                {"batch_id": "batch-20260406-0002", "queue_ref": "ray-queue-123", "status": "completed"}
            ],
            "baseline": {
                "aggregate": 0.1213,
                "by_dataset": {"ds_main": 0.1208, "ds_holdout": 0.1225},
            },
            "completed_experiments": [
                {"batch_id": "batch-20260401-0004", "aggregate": 0.1279},
                {
                    "batch_id": "batch-20260406-0002",
                    "proposal_id": "market-forecast-v3-p16",
                    "aggregate": 0.1213,
                    "judgment": "improvement",
                },
            ],
            "key_learnings": [
                "Leakage checks must run in local sanity before enqueue",
                "Rolling validation tightened the aggregate metric.",
            ],
            "no_improve_iterations": 0,
            "runtime_capabilities": {
                "verified_at": "2026-04-06T00:00:00Z",
                "available_skills": ["metaopt-rollover-worker"],
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
        worker_results: dict[str, dict] | None = None,
        executor_events: dict[str, dict] | None = None,
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

        for name, payload in (worker_results or {}).items():
            (worker_results_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        for name, payload in (executor_events or {}).items():
            (executor_events_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")

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

    def test_plan_roll_iteration_emits_rollover_task(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, tasks_dir = self._run(
                Path(tempdir_str),
                mode="plan_roll_iteration",
                state=self._base_state(),
            )

            self.assertEqual(payload["phase"], "PLAN_ROLL_ITERATION")
            self.assertEqual(payload["outcome"], "planned")
            self.assertEqual(payload["worker_kind"], "custom_agent")
            self.assertEqual(payload["worker_ref"], "metaopt-rollover-worker")
            self.assertEqual(payload["recommended_next_machine_state"], "ROLL_ITERATION")
            task_file = tasks_dir / "rollover-iter-3.md"
            self.assertTrue(task_file.exists())
            task_text = task_file.read_text(encoding="utf-8")
            self.assertIn("metaopt-rollover-worker", task_text)
            self.assertIn("Objective Context", task_text)
            self.assertIn("Proposal Context", task_text)
            self.assertIn("Analysis Context", task_text)
            self.assertIn("Stop Progress Context", task_text)
            self.assertIn("Expected JSON fields", task_text)
            self.assertEqual(updated_state["machine_state"], "ROLL_ITERATION")

    def test_gate_roll_iteration_continuing_clears_selected_experiment_and_advances_to_quiesce(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=self._base_state(),
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [
                            {
                                "proposal_id": "market-forecast-v3-p17",
                                "source_slot_id": "bg-1",
                                "creation_iteration": 3,
                                "created_at": "2026-04-06T09:00:00Z",
                                "title": "Try stricter validation cutoff",
                                "rationale": "Reduce temporal leakage margin",
                                "expected_impact": {"direction": "improve", "magnitude": "small"},
                                "target_area": "validation",
                            }
                        ],
                        "merged_proposals": [],
                        "needs_fresh_ideation": False,
                        "summary": {"carried_over": 1, "discarded": 0, "merged": 0, "final_pool_size": 1},
                    }
                },
            )

            self.assertEqual(payload["outcome"], "rollover_complete")
            self.assertTrue(payload["continue_campaign"])
            self.assertEqual(updated_state["machine_state"], "QUIESCE_SLOTS")
            self.assertIsNone(updated_state["selected_experiment"])
            self.assertEqual(updated_state["current_iteration"], 4)
            self.assertEqual(len(updated_state["current_proposals"]), 1)
            self.assertEqual(updated_state["next_proposals"], [])
            self.assertIn("Iteration 3 Report", updated_state["last_iteration_report"])

    def test_gate_roll_iteration_enriches_merged_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=self._base_state(),
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [],
                        "merged_proposals": [
                            {
                                "title": "Merge two validation ideas",
                                "rationale": "Combine the strongest cutoff and split hygiene ideas",
                                "expected_impact": {"direction": "improve", "magnitude": "medium"},
                                "target_area": "validation",
                            }
                        ],
                        "needs_fresh_ideation": False,
                        "summary": {"carried_over": 0, "discarded": 1, "merged": 1, "final_pool_size": 1},
                    }
                },
            )

            merged = updated_state["current_proposals"][0]
            self.assertEqual(payload["outcome"], "rollover_complete")
            self.assertEqual(merged["source_slot_id"], "rollover")
            self.assertEqual(merged["creation_iteration"], 4)
            self.assertTrue(merged["proposal_id"].startswith("market-forecast-v3-p"))

    def test_gate_roll_iteration_stops_on_target_metric_without_incrementing_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["baseline"]["aggregate"] = 0.1199
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=state,
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [],
                        "merged_proposals": [],
                        "needs_fresh_ideation": False,
                        "summary": {"carried_over": 0, "discarded": 1, "merged": 0, "final_pool_size": 0},
                    }
                },
            )

            self.assertFalse(payload["continue_campaign"])
            self.assertEqual(payload["stop_reason"], "target_metric")
            self.assertEqual(updated_state["current_iteration"], 3)
            self.assertEqual(updated_state["machine_state"], "QUIESCE_SLOTS")

    def test_quiesce_slots_routes_continue_and_appends_apply_results(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["current_iteration"] = 4
            state["selected_experiment"] = None
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-4": {
                        "continue_campaign": True,
                        "stop_reason": "",
                        "finished_slots": ["bg-1"],
                        "canceled_slots": [{"slot_id": "bg-2", "reason": "drain timeout"}],
                        "drain_duration_seconds": 60,
                        "maintenance_apply_results": [
                            {
                                "patch_path": ".ml-metaopt/artifacts/patches/maintenance/bg-1.patch",
                                "status": "applied",
                                "error": None,
                            }
                        ],
                        "summary": "drained one slot and canceled one slot",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "continue")
            self.assertEqual(updated_state["machine_state"], "MAINTAIN_BACKGROUND_POOL")
            self.assertEqual(updated_state["status"], "RUNNING")
            self.assertEqual(updated_state["active_slots"], [])
            self.assertEqual(updated_state["local_changeset"]["apply_results"][-1]["patch_path"], ".ml-metaopt/artifacts/patches/maintenance/bg-1.patch")

    def test_quiesce_slots_routes_stop_to_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["selected_experiment"] = None
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-3": {
                        "continue_campaign": False,
                        "stop_reason": "target_metric",
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 5,
                        "maintenance_apply_results": [],
                        "summary": "all work drained cleanly",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "complete")
            self.assertEqual(updated_state["status"], "COMPLETE")
            self.assertEqual(updated_state["machine_state"], "COMPLETE")

    def test_gate_roll_iteration_stops_on_max_wallclock_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["campaign_started_at"] = "2026-04-01T00:00:00Z"
            load_handoff = self._write_load_handoff(Path(tempdir_str))
            load_data = json.loads(load_handoff.read_text(encoding="utf-8"))
            load_data["stop_conditions"]["max_wallclock_hours"] = 0.001
            load_handoff.write_text(json.dumps(load_data), encoding="utf-8")

            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=state,
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [],
                        "merged_proposals": [],
                        "needs_fresh_ideation": False,
                        "summary": {"carried_over": 0, "discarded": 0, "merged": 0, "final_pool_size": 0},
                    }
                },
            )

            self.assertFalse(payload["continue_campaign"])
            self.assertEqual(payload["stop_reason"], "max_wallclock_hours")
            self.assertEqual(updated_state["current_iteration"], 3)

    def test_quiesce_complete_emits_terminal_cleanup_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["selected_experiment"] = None
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-3": {
                        "continue_campaign": False,
                        "stop_reason": "target_metric",
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 5,
                        "maintenance_apply_results": [],
                        "summary": "all work drained cleanly",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "complete")
            directives = payload["executor_directives"]
            self.assertEqual(
                [directive["action"] for directive in directives],
                ["remove_agents_hook", "delete_state_file", "emit_final_report"],
            )
            remove_hook = directives[0]
            self.assertEqual(remove_hook["agents_path"], "AGENTS.md")
            delete_state = directives[1]
            self.assertEqual(delete_state["state_path"], ".ml-metaopt/state.json")
            emit_final = directives[2]
            self.assertEqual(emit_final["report_type"], "final")

    def test_quiesce_blocked_protocol_preserves_state_and_removes_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["selected_experiment"] = None
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-3": {
                        "continue_campaign": False,
                        "blocked_protocol": True,
                        "stop_reason": "protocol_violation",
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 5,
                        "maintenance_apply_results": [],
                        "summary": "protocol cannot represent next step",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "blocked_protocol")
            self.assertEqual(updated_state["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["active_slots"], [])
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_PROTOCOL")
            directives = payload["executor_directives"]
            self.assertEqual(
                [directive["action"] for directive in directives],
                ["remove_agents_hook"],
            )
            self.assertEqual(directives[0]["agents_path"], "AGENTS.md")

    def test_quiesce_blocked_protocol_does_not_delete_state_or_emit_report(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["selected_experiment"] = None
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-3": {
                        "continue_campaign": False,
                        "blocked_protocol": True,
                        "stop_reason": "protocol_violation",
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 5,
                        "maintenance_apply_results": [],
                        "summary": "protocol cannot represent next step",
                    }
                },
            )

            directive_actions = [d["action"] for d in payload["executor_directives"]]
            self.assertNotIn("delete_state_file", directive_actions)
            self.assertNotIn("emit_final_report", directive_actions)

    # ── drift regression: protocol gap blocks instead of inventing ────

    def test_regression_quiesce_blocked_preserves_state_for_human_review(self) -> None:
        """Drift temptation: on BLOCKED_PROTOCOL, the iteration close control
        might clear state, delete artefacts, or invent a recovery lane.
        The correct behaviour is to preserve state intact (except status/machine_state)
        so a human can inspect and resume."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["selected_experiment"] = None
            original_baseline = dict(state["baseline"])
            original_completed = list(state["completed_experiments"])
            original_learnings = list(state["key_learnings"])

            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-3": {
                        "continue_campaign": False,
                        "blocked_protocol": True,
                        "stop_reason": "protocol_violation",
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 5,
                        "maintenance_apply_results": [],
                        "summary": "protocol cannot represent next step",
                    }
                },
            )

            self.assertEqual(updated_state["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_PROTOCOL")
            # Campaign progress must be preserved for human review
            self.assertEqual(updated_state["baseline"], original_baseline,
                "baseline must be preserved on BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["completed_experiments"], original_completed,
                "completed_experiments must be preserved on BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["key_learnings"], original_learnings,
                "key_learnings must be preserved on BLOCKED_PROTOCOL")
            # No invented workers
            self.assertEqual(payload["launch_requests"], [],
                "BLOCKED_PROTOCOL must not launch recovery workers")

    def test_regression_rollover_gap_blocks_not_invents_new_iteration(self) -> None:
        """Drift temptation: gate_roll_iteration is called without a rollover
        worker result.  The control agent must emit a runtime_error rather than
        inventing a synthetic rollover or advancing the iteration counter."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            original_iteration = state["current_iteration"]
            # No rollover worker result provided
            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=state,
            )

            # Must be runtime_error — not a synthetic advance
            self.assertEqual(payload["outcome"], "runtime_error")
            # Iteration must NOT have been incremented
            self.assertEqual(updated_state["current_iteration"], original_iteration,
                "iteration must not advance without rollover worker output")
            # No launch requests for invented workers
            self.assertEqual(payload["launch_requests"], [],
                "runtime_error must not spawn workers")

    def test_regression_malformed_rollover_result_blocks_protocol(self) -> None:
        """Drift temptation: accept a half-written rollover payload and keep
        going. The control agent must fail closed instead of treating missing
        contract fields as empty defaults."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            original_iteration = state["current_iteration"]
            original_selected = state["selected_experiment"]["proposal_id"]
            original_pool = list(state["next_proposals"])

            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=state,
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [],
                        # missing merged_proposals + needs_fresh_ideation
                        "summary": {"carried_over": 0, "discarded": 0, "merged": 0, "final_pool_size": 0},
                    }
                },
            )

            self.assertEqual(payload["outcome"], "blocked_protocol")
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["current_iteration"], original_iteration)
            self.assertEqual(updated_state["selected_experiment"]["proposal_id"], original_selected)
            self.assertEqual(updated_state["next_proposals"], original_pool)
            self.assertEqual(payload["launch_requests"], [])

    def test_regression_quiesce_requires_explicit_routing_event(self) -> None:
        """Drift temptation: interpret an underspecified quiesce event as a
        clean stop. The control agent must block rather than invent continue vs
        complete semantics from missing fields."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["selected_experiment"] = None
            original_completed = list(state["completed_experiments"])
            original_baseline = dict(state["baseline"])

            payload, updated_state, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-3": {
                        # missing continue_campaign/blocked_protocol and drain metadata
                        "summary": "executor wrote an incomplete quiesce event",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "blocked_protocol")
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["status"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["machine_state"], "BLOCKED_PROTOCOL")
            self.assertEqual(updated_state["completed_experiments"], original_completed)
            self.assertEqual(updated_state["baseline"], original_baseline)
            self.assertEqual(payload["launch_requests"], [])

    def test_quiesce_continue_has_no_terminal_cleanup_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["current_iteration"] = 4
            state["selected_experiment"] = None
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-4": {
                        "continue_campaign": True,
                        "stop_reason": "",
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 5,
                        "maintenance_apply_results": [],
                        "summary": "all work drained cleanly",
                    }
                },
            )

            self.assertEqual(payload["outcome"], "continue")
            self.assertEqual(payload["executor_directives"], [])

    # ── executor_directives authoritativeness tests ─────────────────────

    def test_plan_roll_iteration_has_no_executor_directives(self) -> None:
        """PLAN_ROLL_ITERATION only launches a worker; no executor-side work."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_roll_iteration",
                state=self._base_state(),
            )
            self.assertEqual(payload["executor_directives"], [])

    def test_gate_roll_iteration_continuing_emits_executor_directives(self) -> None:
        """GATE_ROLL_ITERATION must tell the executor to emit_iteration_report, drain_slots, cancel_slots."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=self._base_state(),
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [],
                        "merged_proposals": [],
                        "needs_fresh_ideation": False,
                        "summary": {"carried_over": 0, "discarded": 0, "merged": 0, "final_pool_size": 0},
                    }
                },
            )

            self.assertTrue(payload["continue_campaign"])
            directives = payload["executor_directives"]
            self.assertEqual(
                [directive["action"] for directive in directives],
                ["emit_iteration_report", "drain_slots", "cancel_slots"],
            )
            report_dir = directives[0]
            self.assertEqual(report_dir["report_type"], "iteration")
            self.assertEqual(report_dir["iteration"], 3)
            drain_dir = directives[1]
            self.assertEqual(drain_dir["drain_window_seconds"], 60)
            cancel_dir = directives[2]
            self.assertEqual(cancel_dir["slot_ids"], ["bg-1", "bg-2"])
            for d in directives:
                self.assertIn("reason", d)
                self.assertTrue(d["reason"])

    def test_gate_roll_iteration_stopping_emits_executor_directives(self) -> None:
        """When stop_reason is set, gate still emits the quiesce directives."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["baseline"]["aggregate"] = 0.1199
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=state,
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [],
                        "merged_proposals": [],
                        "needs_fresh_ideation": False,
                        "summary": {"carried_over": 0, "discarded": 0, "merged": 0, "final_pool_size": 0},
                    }
                },
            )

            self.assertFalse(payload["continue_campaign"])
            directives = payload["executor_directives"]
            actions = [d["action"] for d in directives]
            self.assertIn("emit_iteration_report", actions)
            self.assertIn("drain_slots", actions)
            self.assertIn("cancel_slots", actions)

    def test_gate_roll_iteration_emit_iteration_report_directive_carries_iteration(self) -> None:
        """emit_iteration_report directive must reference the completed iteration."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="gate_roll_iteration",
                state=self._base_state(),
                worker_results={
                    "rollover-iter-3": {
                        "filtered_proposals": [],
                        "merged_proposals": [],
                        "needs_fresh_ideation": False,
                        "summary": {"carried_over": 0, "discarded": 0, "merged": 0, "final_pool_size": 0},
                    }
                },
            )

            report_dir = next(d for d in payload["executor_directives"] if d["action"] == "emit_iteration_report")
            self.assertEqual(report_dir["iteration"], 3)

    def test_runtime_error_has_no_executor_directives(self) -> None:
        """Runtime errors are informational; they must not carry executor work."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["selected_experiment"] = None
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_roll_iteration",
                state=state,
            )
            self.assertEqual(payload["outcome"], "runtime_error")
            self.assertEqual(payload["executor_directives"], [])

    def _assert_envelope_keys(self, payload: dict, *, handoff_type: str = "ITERATION_CLOSE_CONTROL", control_agent: str = "metaopt-iteration-close-control") -> None:
        self.assertEqual(payload["handoff_type"], handoff_type)
        self.assertEqual(payload["control_agent"], control_agent)
        self.assertIsInstance(payload["launch_requests"], list)
        self.assertIsInstance(payload["state_patch"], dict)
        self.assertIsInstance(payload["executor_directives"], list)
        self.assertIn("summary", payload)
        self.assertIn("warnings", payload)
        self.assertIn("recommended_next_machine_state", payload)

    def test_plan_roll_iteration_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="plan_roll_iteration",
                state=self._base_state(),
            )
            self._assert_envelope_keys(payload)

    def test_quiesce_slots_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            state = self._base_state()
            state["machine_state"] = "QUIESCE_SLOTS"
            state["selected_experiment"] = None
            payload, _, _ = self._run(
                Path(tempdir_str),
                mode="quiesce_slots",
                state=state,
                executor_events={
                    "quiesce-slots-iter-3": {
                        "continue_campaign": False,
                        "stop_reason": "target_metric",
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 5,
                        "maintenance_apply_results": [],
                        "summary": "all work drained cleanly",
                    }
                },
            )
            self._assert_envelope_keys(payload)

    def test_agent_profile_exists_and_declares_all_modes(self) -> None:
        self.assertTrue(AGENT_PROFILE.exists(), f"missing {AGENT_PROFILE}")
        content = AGENT_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-iteration-close-control", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("plan_roll_iteration", content)
        self.assertIn("gate_roll_iteration", content)
        self.assertIn("quiesce_slots", content)
        self.assertIn("scripts/iteration_close_control_handoff.py", content)

    def test_rollover_worker_profile_exists_and_is_leaf_only(self) -> None:
        self.assertTrue(WORKER_PROFILE.exists(), f"missing {WORKER_PROFILE}")
        content = WORKER_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-rollover-worker", content)
        self.assertIn("model: gpt-5.4", content)
        self.assertIn("Do not launch subagents.", content)
        self.assertIn("Do not mutate `.ml-metaopt/state.json`.", content)


if __name__ == "__main__":
    unittest.main()

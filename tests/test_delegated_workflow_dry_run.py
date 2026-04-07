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
LOCAL_SCRIPT = ROOT / "scripts" / "local_execution_control_handoff.py"
REMOTE_SCRIPT = ROOT / "scripts" / "remote_execution_control_handoff.py"
ITERATION_SCRIPT = ROOT / "scripts" / "iteration_close_control_handoff.py"


class DelegatedWorkflowDryRunTests(unittest.TestCase):
    def _write_campaign(self, tempdir: Path) -> Path:
        campaign_path = tempdir / "ml_metaopt_campaign.yaml"
        payload = {
            "version": 3,
            "campaign_id": "market-forecast-v3",
            "goal": "Improve out-of-sample forecast quality without temporal leakage.",
            "objective": {
                "metric": "rmse",
                "direction": "minimize",
                "aggregation": {"method": "weighted_mean", "weights": {"ds_main": 0.7, "ds_holdout": 0.3}},
                "improvement_threshold": 0.0005,
            },
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
            "baseline": {
                "aggregate": 0.1284,
                "by_dataset": {"ds_main": 0.1269, "ds_holdout": 0.1320},
            },
            "stop_conditions": {
                "max_iterations": 20,
                "max_no_improve_iterations": 4,
                "target_metric": 0.1200,
                "max_wallclock_hours": 72,
            },
            "proposal_policy": {
                "current_target": 3,
                "current_floor": 2,
                "next_cap": 5,
                "distinctness_rule": "non_overlapping",
            },
            "dispatch_policy": {"background_slots": 2, "auxiliary_slots": 2},
            "sanity": {
                "command": "python3 scripts/local_sanity.py --fast",
                "max_duration_seconds": 60,
                "require_zero_temporal_leakage": True,
                "require_config_load": True,
            },
            "artifacts": {
                "code_roots": ["."],
                "data_roots": ["data"],
                "exclude": [".git", ".venv", "logs", ".ml-metaopt"],
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
                "trial_budget": {"kind": "fixed_trials", "value": 128},
                "search_strategy": {"kind": "optuna_tpe", "seed": 1337},
            },
        }
        campaign_path.write_text(json.dumps(payload), encoding="utf-8")
        return campaign_path

    def _write_skills_manifest(self, tempdir: Path) -> Path:
        manifest_path = tempdir / "agents" / "worker-skills.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "skills": [
                {
                    "name": "metaopt-ideation-worker",
                    "lane": "ideation",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-ideation-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-ideation-worker.agent.md")],
                },
                {
                    "name": "metaopt-selection-worker",
                    "lane": "selection",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-selection-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-selection-worker.agent.md")],
                },
                {
                    "name": "metaopt-design-worker",
                    "lane": "design",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-design-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-design-worker.agent.md")],
                },
                {
                    "name": "metaopt-materialization-worker",
                    "lane": "materialization",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-materialization-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-materialization-worker.agent.md")],
                },
                {
                    "name": "metaopt-diagnosis-worker",
                    "lane": "diagnosis",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-diagnosis-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-diagnosis-worker.agent.md")],
                },
                {
                    "name": "metaopt-analysis-worker",
                    "lane": "analysis",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-analysis-worker",
                    "classification": "required",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-analysis-worker.agent.md")],
                },
                {
                    "name": "metaopt-rollover-worker",
                    "lane": "rollover",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-rollover-worker",
                    "classification": "degradable",
                    "degraded_lane": "rollover",
                    "probe_paths": [str(ROOT / ".github" / "agents" / "metaopt-rollover-worker.agent.md")],
                },
                {
                    "name": "repo-audit-refactor-optimize",
                    "lane": "maintenance",
                    "worker_kind": "skill",
                    "worker_ref": "repo-audit-refactor-optimize",
                    "classification": "degradable",
                    "degraded_lane": "maintenance",
                    "probe_paths": ["/home/jakub/.agents/skills/repo-audit-refactor-optimize/SKILL.md"],
                },
            ]
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return manifest_path

    # Universal envelope keys required by references/control-protocol.md
    _ENVELOPE_KEYS = {
        "handoff_type",
        "control_agent",
        "recommended_next_machine_state",
        "launch_requests",
        "state_patch",
        "executor_directives",
        "summary",
        "warnings",
    }

    _CONTROL_AGENTS = {
        "metaopt-load-campaign",
        "metaopt-hydrate-state",
        "metaopt-background-control",
        "metaopt-select-design",
        "metaopt-local-execution-control",
        "metaopt-remote-execution-control",
        "metaopt-iteration-close-control",
    }

    def _assert_envelope(self, payload: dict, expected_agent: str, label: str) -> None:
        """Validate that *payload* conforms to the universal control-handoff envelope."""
        missing = self._ENVELOPE_KEYS - payload.keys()
        self.assertFalse(missing, f"{label}: missing envelope keys {missing}")

        self.assertIsInstance(payload["handoff_type"], str, f"{label}: handoff_type must be a string")
        self.assertTrue(payload["handoff_type"], f"{label}: handoff_type must be non-empty")

        self.assertEqual(payload["control_agent"], expected_agent, f"{label}: wrong control_agent")
        self.assertIn(payload["control_agent"], self._CONTROL_AGENTS, f"{label}: unknown control_agent")

        rec = payload["recommended_next_machine_state"]
        self.assertTrue(rec is None or isinstance(rec, str), f"{label}: recommended_next_machine_state must be str|null")

        self.assertIsInstance(payload["launch_requests"], list, f"{label}: launch_requests must be a list")
        self.assertTrue(
            payload["state_patch"] is None or isinstance(payload["state_patch"], dict),
            f"{label}: state_patch must be dict|null",
        )
        self.assertIsInstance(payload["executor_directives"], list, f"{label}: executor_directives must be a list")
        for i, d in enumerate(payload["executor_directives"]):
            self.assertIsInstance(d, dict, f"{label}: executor_directives[{i}] must be a dict")
            self.assertIn("action", d, f"{label}: executor_directives[{i}] missing 'action'")
            self.assertIsInstance(d["action"], str, f"{label}: executor_directives[{i}] action must be str")
            self.assertTrue(d["action"], f"{label}: executor_directives[{i}] action must be non-empty")
            self.assertIn("reason", d, f"{label}: executor_directives[{i}] missing 'reason'")
            self.assertIsInstance(d["reason"], str, f"{label}: executor_directives[{i}] reason must be str")
            self.assertTrue(d["reason"], f"{label}: executor_directives[{i}] reason must be non-empty")
        self.assertIsInstance(payload["summary"], str, f"{label}: summary must be a string")
        self.assertIsInstance(payload["warnings"], list, f"{label}: warnings must be a list")

    def _run(self, cmd: list[str], output_path: Path) -> dict:
        completed = subprocess.run(
            cmd,
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
        return payload

    # ── directive normalizer unit tests ──────────────────────────────

    def test_normalize_directives_returns_empty_list_for_none(self) -> None:
        from _handoff_utils import normalize_directives
        self.assertEqual(normalize_directives(None), [])

    def test_normalize_directives_passes_valid_list(self) -> None:
        from _handoff_utils import normalize_directives
        valid = [{"action": "do_x", "reason": "because_y"}]
        self.assertEqual(normalize_directives(valid), valid)

    def test_normalize_directives_preserves_extra_keys(self) -> None:
        from _handoff_utils import normalize_directives
        d = [{"action": "a", "reason": "r", "extra": 42}]
        result = normalize_directives(d)
        self.assertEqual(result[0]["extra"], 42)

    def test_normalize_directives_rejects_non_list(self) -> None:
        from _handoff_utils import normalize_directives
        with self.assertRaises(TypeError):
            normalize_directives("bad")

    def test_normalize_directives_rejects_non_dict_element(self) -> None:
        from _handoff_utils import normalize_directives
        with self.assertRaises(TypeError):
            normalize_directives(["not a dict"])

    def test_normalize_directives_rejects_missing_action(self) -> None:
        from _handoff_utils import normalize_directives
        with self.assertRaises(ValueError):
            normalize_directives([{"reason": "r"}])

    def test_normalize_directives_rejects_empty_action(self) -> None:
        from _handoff_utils import normalize_directives
        with self.assertRaises(ValueError):
            normalize_directives([{"action": "", "reason": "r"}])

    def test_normalize_directives_rejects_missing_reason(self) -> None:
        from _handoff_utils import normalize_directives
        with self.assertRaises(ValueError):
            normalize_directives([{"action": "a"}])

    def test_normalize_directives_rejects_empty_reason(self) -> None:
        from _handoff_utils import normalize_directives
        with self.assertRaises(ValueError):
            normalize_directives([{"action": "a", "reason": ""}])

    def test_emit_handoff_normalizes_directives(self) -> None:
        from _handoff_utils import emit_handoff
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "h.json"
            payload = emit_handoff(
                out,
                {"executor_directives": [{"action": "a", "reason": "r"}]},
                handoff_type="TEST",
                control_agent="test-agent",
            )
            self.assertEqual(payload["executor_directives"], [{"action": "a", "reason": "r"}])

    def test_emit_handoff_defaults_to_empty_directives(self) -> None:
        from _handoff_utils import emit_handoff
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "h.json"
            payload = emit_handoff(out, {}, handoff_type="TEST", control_agent="test-agent")
            self.assertEqual(payload["executor_directives"], [])

    def test_emit_handoff_rejects_bad_directives(self) -> None:
        from _handoff_utils import emit_handoff
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "h.json"
            with self.assertRaises((TypeError, ValueError)):
                emit_handoff(
                    out,
                    {"executor_directives": [{"action": "a"}]},
                    handoff_type="TEST",
                    control_agent="test-agent",
                )

    # ── full delegated workflow integration test ─────────────────────

    def test_full_delegated_workflow_reaches_complete_via_staged_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign_path = self._write_campaign(tempdir)
            skills_manifest = self._write_skills_manifest(tempdir)
            state_path = tempdir / ".ml-metaopt" / "state.json"
            handoffs_dir = tempdir / ".ml-metaopt" / "handoffs"
            tasks_dir = tempdir / ".ml-metaopt" / "tasks"
            worker_results_dir = tempdir / ".ml-metaopt" / "worker-results"
            slot_events_dir = tempdir / ".ml-metaopt" / "slot-events"
            executor_events_dir = tempdir / ".ml-metaopt" / "executor-events"
            agents_path = tempdir / "AGENTS.md"
            handoffs_dir.mkdir(parents=True, exist_ok=True)
            tasks_dir.mkdir(parents=True, exist_ok=True)
            worker_results_dir.mkdir(parents=True, exist_ok=True)
            slot_events_dir.mkdir(parents=True, exist_ok=True)
            executor_events_dir.mkdir(parents=True, exist_ok=True)

            load_output = handoffs_dir / "load_campaign.latest.json"
            load_payload = self._run(
                [
                    "python3",
                    str(LOAD_SCRIPT),
                    "--campaign-path",
                    str(campaign_path),
                    "--state-path",
                    str(state_path),
                    "--output",
                    str(load_output),
                ],
                load_output,
            )
            self.assertEqual(load_payload["outcome"], "ok")
            self._assert_envelope(load_payload, "metaopt-load-campaign", "load_campaign")

            hydrate_output = handoffs_dir / "hydrate_state.latest.json"
            hydrate_payload = self._run(
                [
                    "python3",
                    str(HYDRATE_SCRIPT),
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--agents-path",
                    str(agents_path),
                    "--skills-manifest",
                    str(skills_manifest),
                    "--output",
                    str(hydrate_output),
                ],
                hydrate_output,
            )
            self.assertEqual(hydrate_payload["outcome"], "initialized")
            self._assert_envelope(hydrate_payload, "metaopt-hydrate-state", "hydrate_state")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["machine_state"], "MAINTAIN_BACKGROUND_POOL")

            bg_plan_output = handoffs_dir / "plan_background_work.latest.json"
            bg_plan = self._run(
                [
                    "python3",
                    str(BACKGROUND_SCRIPT),
                    "--mode",
                    "plan_background_work",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--slot-events-dir",
                    str(slot_events_dir),
                    "--output",
                    str(bg_plan_output),
                ],
                bg_plan_output,
            )
            self.assertEqual(bg_plan["phase"], "PLAN_BACKGROUND_WORK")
            self.assertEqual(len(bg_plan["launch_requests"]), 2)
            self._assert_envelope(bg_plan, "metaopt-background-control", "plan_background_work")

            ideation_candidates = [
                {
                    "title": "Tighten rolling split",
                    "rationale": "Lower leakage risk",
                    "expected_impact": {"direction": "improve", "magnitude": "medium"},
                    "target_area": "validation",
                },
                {
                    "title": "Add lag features",
                    "rationale": "Improve temporal signal extraction",
                    "expected_impact": {"direction": "improve", "magnitude": "small"},
                    "target_area": "features",
                },
            ]
            for request in bg_plan["launch_requests"]:
                slot_id = request["slot_id"]
                (slot_events_dir / f"{slot_id}.json").write_text(
                    json.dumps({"slot_id": slot_id, "status": "completed", "result_file": f"{slot_id}.json"}),
                    encoding="utf-8",
                )
                (worker_results_dir / f"{slot_id}.json").write_text(
                    json.dumps(
                        {
                            "slot_id": slot_id,
                            "mode": "ideation",
                            "status": "completed",
                            "summary": "two candidates",
                            "proposal_candidates": ideation_candidates,
                        }
                    ),
                    encoding="utf-8",
                )

            bg_gate_output = handoffs_dir / "gate_background_work.latest.json"
            bg_gate = self._run(
                [
                    "python3",
                    str(BACKGROUND_SCRIPT),
                    "--mode",
                    "gate_background_work",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--slot-events-dir",
                    str(slot_events_dir),
                    "--output",
                    str(bg_gate_output),
                ],
                bg_gate_output,
            )
            self.assertEqual(bg_gate["recommended_next_machine_state"], "SELECT_EXPERIMENT")
            self._assert_envelope(bg_gate, "metaopt-background-control", "gate_background_work")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["machine_state"], "MAINTAIN_BACKGROUND_POOL")
            self.assertEqual(state["next_action"], "select experiment")
            self.assertGreaterEqual(len(state["current_proposals"]), 3)

            select_plan_output = handoffs_dir / "select_and_design.latest.json"
            select_plan = self._run(
                [
                    "python3",
                    str(SELECT_SCRIPT),
                    "--mode",
                    "plan_select_experiment",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--output",
                    str(select_plan_output),
                ],
                select_plan_output,
            )
            self.assertEqual(select_plan["worker_ref"], "metaopt-selection-worker")
            self._assert_envelope(select_plan, "metaopt-select-design", "plan_select_experiment")
            selected_proposal = json.loads(state_path.read_text(encoding="utf-8"))["current_proposals"][0]
            (worker_results_dir / "select-experiment-iter-1.json").write_text(
                json.dumps(
                    {
                        "winning_proposal": selected_proposal,
                        "ranking_rationale": "Validation-focused work is the best first improvement.",
                    }
                ),
                encoding="utf-8",
            )

            design_plan = self._run(
                [
                    "python3",
                    str(SELECT_SCRIPT),
                    "--mode",
                    "gate_select_and_plan_design",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--output",
                    str(select_plan_output),
                ],
                select_plan_output,
            )
            self.assertEqual(design_plan["worker_ref"], "metaopt-design-worker")
            self._assert_envelope(design_plan, "metaopt-select-design", "gate_select_and_plan_design")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["machine_state"], "DESIGN_EXPERIMENT")
            self.assertIsNone(state["selected_experiment"]["design"])

            (worker_results_dir / "design-experiment-iter-1.json").write_text(
                json.dumps(
                    {
                        "proposal_id": selected_proposal["proposal_id"],
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
                ),
                encoding="utf-8",
            )

            select_finalize = self._run(
                [
                    "python3",
                    str(SELECT_SCRIPT),
                    "--mode",
                    "finalize_select_design",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--output",
                    str(select_plan_output),
                ],
                select_plan_output,
            )
            self.assertEqual(select_finalize["recommended_next_machine_state"], "MATERIALIZE_CHANGESET")
            self._assert_envelope(select_finalize, "metaopt-select-design", "finalize_select_design")

            local_output = handoffs_dir / "local_execution.latest.json"
            local_plan = self._run(
                [
                    "python3",
                    str(LOCAL_SCRIPT),
                    "--mode",
                    "plan_local_changeset",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(local_output),
                ],
                local_output,
            )
            self.assertEqual(local_plan["worker_ref"], "metaopt-materialization-worker")
            self._assert_envelope(local_plan, "metaopt-local-execution-control", "plan_local_changeset")
            (worker_results_dir / "materialization-1.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "patch_artifacts": [
                            {
                                "producer_slot_id": "aux-1",
                                "purpose": "candidate patch bundle",
                                "patch_path": ".ml-metaopt/artifacts/patches/batch-20260407-0001/aux-1.patch",
                                "target_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                            }
                        ],
                        "verification_notes": ["pytest -q passed"],
                        "summary": "materialized validation change",
                    }
                ),
                encoding="utf-8",
            )
            (executor_events_dir / "local_changeset-1.json").write_text(
                json.dumps(
                    {
                        "integration_worktree": ".ml-metaopt/worktrees/iter-1-materialization",
                        "apply_results": [
                            {
                                "patch_path": ".ml-metaopt/artifacts/patches/batch-20260407-0001/aux-1.patch",
                                "status": "applied",
                                "error": None,
                            }
                        ],
                        "code_artifact_uri": ".ml-metaopt/artifacts/code/batch-20260407-0001.tar.gz",
                        "data_manifest_uri": ".ml-metaopt/artifacts/data/batch-20260407-0001.json",
                    }
                ),
                encoding="utf-8",
            )
            (executor_events_dir / "sanity-1.json").write_text(
                json.dumps({"status": "passed", "exit_code": 0, "stdout": "ok", "stderr": "", "duration_seconds": 12}),
                encoding="utf-8",
            )
            local_gate = self._run(
                [
                    "python3",
                    str(LOCAL_SCRIPT),
                    "--mode",
                    "gate_local_sanity",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(local_output),
                ],
                local_output,
            )
            self.assertEqual(local_gate["recommended_next_machine_state"], "ENQUEUE_REMOTE_BATCH")
            self._assert_envelope(local_gate, "metaopt-local-execution-control", "gate_local_sanity")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["machine_state"], "ENQUEUE_REMOTE_BATCH")
            self.assertIsInstance(state["local_changeset"], dict)

            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            batch_id = f"batch-{today}-0001"
            remote_output = handoffs_dir / "remote_execution.latest.json"
            remote_plan = self._run(
                [
                    "python3",
                    str(REMOTE_SCRIPT),
                    "--mode",
                    "plan_remote_batch",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(remote_output),
                ],
                remote_output,
            )
            self.assertEqual(remote_plan["batch_id"], batch_id)
            self._assert_envelope(remote_plan, "metaopt-remote-execution-control", "plan_remote_batch")
            (executor_events_dir / f"enqueue-{batch_id}.json").write_text(
                json.dumps({"batch_id": batch_id, "queue_ref": "ray-queue-123", "status": "queued"}),
                encoding="utf-8",
            )
            remote_gate = self._run(
                [
                    "python3",
                    str(REMOTE_SCRIPT),
                    "--mode",
                    "gate_remote_batch",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(remote_output),
                ],
                remote_output,
            )
            self.assertEqual(remote_gate["recommended_next_machine_state"], "WAIT_FOR_REMOTE_BATCH")
            self._assert_envelope(remote_gate, "metaopt-remote-execution-control", "gate_remote_batch_initial")
            (executor_events_dir / f"remote-status-{batch_id}.json").write_text(
                json.dumps(
                    {
                        "batch_id": batch_id,
                        "status": "completed",
                        "timestamps": {"queued_at": "2026-04-07T10:00:00Z", "started_at": "2026-04-07T10:02:00Z"},
                    }
                ),
                encoding="utf-8",
            )
            (executor_events_dir / f"remote-results-{batch_id}.json").write_text(
                json.dumps(
                    {
                        "batch_id": batch_id,
                        "status": "completed",
                        "best_aggregate_result": {"metric": "rmse", "value": 0.1198},
                        "per_dataset": {"ds_main": 0.1191, "ds_holdout": 0.1214},
                        "artifact_locations": {"code": ".ml-metaopt/artifacts/code/out.tar.gz", "data_manifest": ".ml-metaopt/artifacts/data/out.json"},
                        "logs_location": ".ml-metaopt/artifacts/logs/batch.log",
                    }
                ),
                encoding="utf-8",
            )
            remote_analysis_request = self._run(
                [
                    "python3",
                    str(REMOTE_SCRIPT),
                    "--mode",
                    "gate_remote_batch",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(remote_output),
                ],
                remote_output,
            )
            self.assertEqual(remote_analysis_request["worker_ref"], "metaopt-analysis-worker")
            self._assert_envelope(remote_analysis_request, "metaopt-remote-execution-control", "gate_remote_batch_analysis_request")
            (worker_results_dir / f"remote-analysis-{batch_id}.json").write_text(
                json.dumps(
                    {
                        "judgment": "improvement",
                        "new_aggregate": 0.1198,
                        "delta": -0.0086,
                        "learnings": ["Validation-first changes beat feature expansion in early iterations."],
                        "invalidations": [{"proposal_id": selected_proposal["proposal_id"], "reason": "already executed"}],
                        "carry_over_candidates": [{"title": "Try stricter cutoff", "rationale": "follow-on validation refinement"}],
                    }
                ),
                encoding="utf-8",
            )
            analyze_ready = self._run(
                [
                    "python3",
                    str(REMOTE_SCRIPT),
                    "--mode",
                    "gate_remote_batch",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(remote_output),
                ],
                remote_output,
            )
            self.assertEqual(analyze_ready["recommended_next_machine_state"], "ANALYZE_RESULTS")
            self._assert_envelope(analyze_ready, "metaopt-remote-execution-control", "gate_remote_batch_analyze_ready")
            analyzed = self._run(
                [
                    "python3",
                    str(REMOTE_SCRIPT),
                    "--mode",
                    "analyze_remote_results",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(remote_output),
                ],
                remote_output,
            )
            self.assertEqual(analyzed["recommended_next_machine_state"], "ROLL_ITERATION")
            self._assert_envelope(analyzed, "metaopt-remote-execution-control", "analyze_remote_results")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["baseline"]["aggregate"], 0.1198)
            self.assertEqual(state["machine_state"], "ROLL_ITERATION")

            iteration_output = handoffs_dir / "iteration_close.latest.json"
            roll_plan = self._run(
                [
                    "python3",
                    str(ITERATION_SCRIPT),
                    "--mode",
                    "plan_roll_iteration",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(iteration_output),
                ],
                iteration_output,
            )
            self.assertEqual(roll_plan["worker_ref"], "metaopt-rollover-worker")
            self._assert_envelope(roll_plan, "metaopt-iteration-close-control", "plan_roll_iteration")
            (worker_results_dir / "rollover-iter-1.json").write_text(
                json.dumps(
                    {
                        "filtered_proposals": [],
                        "merged_proposals": [
                            {
                                "title": "Try stricter cutoff",
                                "rationale": "follow-on validation refinement",
                                "expected_impact": {"direction": "improve", "magnitude": "small"},
                                "target_area": "validation",
                            }
                        ],
                        "needs_fresh_ideation": False,
                        "summary": "target metric already met; no carry-over needed for continuation",
                    }
                ),
                encoding="utf-8",
            )
            roll_gate = self._run(
                [
                    "python3",
                    str(ITERATION_SCRIPT),
                    "--mode",
                    "gate_roll_iteration",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(iteration_output),
                ],
                iteration_output,
            )
            self.assertFalse(roll_gate["continue_campaign"])
            self.assertEqual(roll_gate["stop_reason"], "target_metric")
            self._assert_envelope(roll_gate, "metaopt-iteration-close-control", "gate_roll_iteration")
            gate_directives = roll_gate["executor_directives"]
            self.assertEqual(
                [directive["action"] for directive in gate_directives],
                ["emit_iteration_report", "drain_slots", "cancel_slots"],
            )
            self.assertEqual(gate_directives[0]["report_type"], "iteration")
            self.assertEqual(gate_directives[0]["iteration"], 1)
            self.assertEqual(gate_directives[1]["drain_window_seconds"], 60)
            self.assertIsInstance(gate_directives[2]["slot_ids"], list)
            self.assertTrue(gate_directives[2]["slot_ids"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["machine_state"], "QUIESCE_SLOTS")
            self.assertIsNone(state["selected_experiment"])

            (executor_events_dir / "quiesce-slots-iter-1.json").write_text(
                json.dumps(
                    {
                        "finished_slots": [],
                        "canceled_slots": [],
                        "drain_duration_seconds": 4,
                        "maintenance_apply_results": [],
                        "continue_campaign": False,
                        "stop_reason": "target_metric",
                        "summary": "all slots drained and target metric satisfied",
                    }
                ),
                encoding="utf-8",
            )
            quiesced = self._run(
                [
                    "python3",
                    str(ITERATION_SCRIPT),
                    "--mode",
                    "quiesce_slots",
                    "--load-handoff",
                    str(load_output),
                    "--state-path",
                    str(state_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--worker-results-dir",
                    str(worker_results_dir),
                    "--executor-events-dir",
                    str(executor_events_dir),
                    "--output",
                    str(iteration_output),
                ],
                iteration_output,
            )
            self.assertEqual(quiesced["recommended_next_machine_state"], "COMPLETE")
            self._assert_envelope(quiesced, "metaopt-iteration-close-control", "quiesce_slots")
            quiesce_directives = quiesced["executor_directives"]
            self.assertEqual(
                [directive["action"] for directive in quiesce_directives],
                ["remove_agents_hook", "delete_state_file", "emit_final_report"],
            )
            self.assertEqual(quiesce_directives[0]["agents_path"], "AGENTS.md")
            self.assertEqual(quiesce_directives[1]["state_path"], ".ml-metaopt/state.json")
            self.assertEqual(quiesce_directives[2]["report_type"], "final")
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(final_state["status"], "COMPLETE")
            self.assertEqual(final_state["machine_state"], "COMPLETE")
            self.assertEqual(final_state["completed_experiments"][-1]["batch_id"], batch_id)


if __name__ == "__main__":
    unittest.main()

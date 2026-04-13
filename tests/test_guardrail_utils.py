from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts"))
from _guardrail_utils import (
    ALLOWED_DIRECTIVE_ACTIONS,
    ALLOWED_SLOT_MODES,
    ALLOWED_WORKERS,
    MODEL_RESOLUTION_ORDER_BY_CLASS,
    PREFERRED_MODEL_BY_CLASS,
    normalize_launch_requests,
    validate_executor_policy,
)


class NormalizeLaunchRequestsTests(unittest.TestCase):
    """Tests for normalize_launch_requests."""

    def test_none_returns_empty_list(self) -> None:
        self.assertEqual(normalize_launch_requests(None), [])

    def test_empty_list_passes(self) -> None:
        self.assertEqual(normalize_launch_requests([]), [])

    def test_non_list_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            normalize_launch_requests("bad")  # type: ignore[arg-type]

    def test_non_dict_entry_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            normalize_launch_requests(["bad"])  # type: ignore[list-item]

    def test_background_slot_rejects_materialization_mode(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "materialization",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-materialization-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        with self.assertRaises(ValueError, msg="background slot must reject materialization mode"):
            normalize_launch_requests([request])

    def test_background_slot_accepts_ideation_mode(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "ideation",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["preferred_model"], "claude-sonnet-4")

    def test_background_slot_rejects_analysis_mode(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "analysis",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        with self.assertRaises(ValueError, msg="background slot must reject analysis mode"):
            normalize_launch_requests([request])

    def test_strong_reasoner_gets_preferred_model(self) -> None:
        request = {
            "slot_id": "ana-1",
            "slot_class": "auxiliary",
            "mode": "analysis",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-analysis-worker",
            "model_class": "strong_reasoner",
            "task_file": ".ml-metaopt/tasks/analysis.md",
            "result_file": ".ml-metaopt/worker-results/analysis.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(result[0]["preferred_model"], "claude-opus-4.6")

    def test_analysis_worker_gets_preferred_model(self) -> None:
        request = {
            "slot_id": "ana-1",
            "slot_class": "auxiliary",
            "mode": "analysis",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-analysis-worker",
            "model_class": "strong_reasoner",
            "task_file": ".ml-metaopt/tasks/analysis.md",
            "result_file": ".ml-metaopt/worker-results/analysis.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(result[0]["preferred_model"], "claude-opus-4.6")

    def test_preferred_model_not_overwritten_when_present(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "ideation",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "general_worker",
            "preferred_model": "claude-haiku-4.5",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(result[0]["preferred_model"], "claude-haiku-4.5")

    def test_unknown_worker_ref_rejected(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "ideation",
            "worker_ref": "evil-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        with self.assertRaises(ValueError):
            normalize_launch_requests([request])

    def test_unknown_slot_class_rejected(self) -> None:
        request = {
            "slot_id": "x-1",
            "slot_class": "unknown",
            "mode": "ideation",
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        with self.assertRaises(ValueError):
            normalize_launch_requests([request])

    def test_unknown_model_class_rejected(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "ideation",
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "turbo_mega",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        with self.assertRaises(ValueError):
            normalize_launch_requests([request])

    def test_missing_required_launch_field_rejected(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "ideation",
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
        }
        with self.assertRaisesRegex(ValueError, "result_file"):
            normalize_launch_requests([request])

    def test_mode_requires_slot_class(self) -> None:
        request = {
            "worker_ref": "metaopt-analysis-worker",
            "model_class": "strong_reasoner",
            "mode": "analysis",
            "task_file": ".ml-metaopt/tasks/analysis.md",
            "result_file": ".ml-metaopt/worker-results/analysis.json",
        }
        with self.assertRaisesRegex(ValueError, "slot_class is required"):
            normalize_launch_requests([request])

    def test_slot_class_requires_mode(self) -> None:
        request = {
            "worker_ref": "metaopt-analysis-worker",
            "model_class": "strong_reasoner",
            "slot_class": "auxiliary",
            "task_file": ".ml-metaopt/tasks/analysis.md",
            "result_file": ".ml-metaopt/worker-results/analysis.json",
        }
        with self.assertRaisesRegex(ValueError, "mode is required"):
            normalize_launch_requests([request])

    def test_worker_mode_mismatch_rejected(self) -> None:
        # metaopt-ideation-worker in auxiliary slot: wrong slot_class for this worker
        request = {
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "general_worker",
            "slot_class": "auxiliary",
            "mode": "analysis",
            "task_file": ".ml-metaopt/tasks/ideation.md",
            "result_file": ".ml-metaopt/worker-results/ideation.json",
        }
        with self.assertRaises(ValueError):
            normalize_launch_requests([request])

    def test_worker_model_class_mismatch_rejected(self) -> None:
        request = {
            "worker_ref": "metaopt-analysis-worker",
            "model_class": "general_worker",
            "slot_class": "auxiliary",
            "mode": "analysis",
            "task_file": ".ml-metaopt/tasks/analysis.md",
            "result_file": ".ml-metaopt/worker-results/analysis.json",
        }
        with self.assertRaisesRegex(ValueError, "model classes"):
            normalize_launch_requests([request])


class ValidateExecutorPolicyTests(unittest.TestCase):
    """Tests for validate_executor_policy."""

    def test_empty_directives_passes(self) -> None:
        result = validate_executor_policy("metaopt-remote-execution-control", "PLAN_REMOTE_BATCH", [])
        self.assertEqual(result, [])

    def test_allowed_action_passes(self) -> None:
        directives = [
            {
                "action": "launch_sweep",
                "reason": "submit sweep",
                "sweep_config": {"method": "bayes", "parameters": {}},
                "sky_task_spec": {"accelerator": "A100:1"},
                "result_file": ".ml-metaopt/worker-results/launch-sweep-iter-1.json",
            }
        ]
        result = validate_executor_policy("metaopt-remote-execution-control", "LAUNCH_SWEEP", directives)
        self.assertEqual(len(result), 1)

    def test_remote_control_rejects_ssh_command(self) -> None:
        directives = [{"action": "ssh_command", "reason": "bypass queue"}]
        with self.assertRaises(ValueError, msg="ssh_command must be rejected as raw-cluster bypass"):
            validate_executor_policy("metaopt-remote-execution-control", "PLAN_REMOTE_BATCH", directives)

    def test_unknown_action_rejected(self) -> None:
        directives = [{"action": "nuke_from_orbit", "reason": "only way to be sure"}]
        with self.assertRaises(ValueError):
            validate_executor_policy("metaopt-local-execution-control", "PLAN_LOCAL", directives)

    def test_all_known_actions_pass(self) -> None:
        sample_fields = {
            "launch_sweep": {"sweep_config": {"method": "bayes", "parameters": {}}, "sky_task_spec": {"accelerator": "A100:1"}, "result_file": ".ml-metaopt/worker-results/launch-sweep-iter-1.json"},
            "poll_sweep": {"sweep_id": "wandb-sweep-abc123", "sky_job_ids": ["sky-job-001"], "result_file": ".ml-metaopt/worker-results/poll-sweep-iter-1.json"},
            "run_smoke_test": {"command": "python train.py --smoke", "result_file": ".ml-metaopt/worker-results/smoke-test-iter-1.json"},
            "remove_agents_hook": {"agents_path": "AGENTS.md"},
            "delete_state_file": {"state_path": ".ml-metaopt/state.json"},
            "emit_final_report": {"report_type": "final"},
            "emit_iteration_report": {"report_type": "iteration", "iteration": 3},
            "none": {},
        }
        for action in sorted(ALLOWED_DIRECTIVE_ACTIONS):
            directives = [{"action": action, "reason": f"testing {action}", **sample_fields[action]}]
            result = validate_executor_policy("test-agent", "TEST", directives)
            self.assertEqual(len(result), 1, f"action {action!r} should be accepted")

    def test_raw_ssh_blocked(self) -> None:
        directives = [{"action": "raw_ssh", "reason": "bypass"}]
        with self.assertRaises(ValueError):
            validate_executor_policy("metaopt-remote-execution-control", None, directives)

    def test_kubectl_exec_blocked(self) -> None:
        directives = [{"action": "kubectl_exec", "reason": "bypass"}]
        with self.assertRaises(ValueError):
            validate_executor_policy("metaopt-remote-execution-control", "PLAN_REMOTE_BATCH", directives)

    def test_missing_directive_field_rejected(self) -> None:
        directives = [{"action": "launch_sweep", "reason": "submit sweep", "sweep_config": {"method": "bayes", "parameters": {}}, "sky_task_spec": {"accelerator": "A100:1"}}]
        with self.assertRaisesRegex(ValueError, "result_file"):
            validate_executor_policy("metaopt-remote-execution-control", "LAUNCH_SWEEP", directives)


class ConstantsSanityTests(unittest.TestCase):
    """Sanity checks for the exported constant sets."""

    def test_allowed_workers_contains_known_agents(self) -> None:
        expected = {
            "metaopt-ideation-worker",
            "metaopt-analysis-worker",
            "skypilot-wandb-worker",
        }
        self.assertEqual(ALLOWED_WORKERS, expected)

    def test_allowed_directive_actions_contains_v4_actions(self) -> None:
        expected = {
            "launch_sweep", "poll_sweep", "run_smoke_test",
            "remove_agents_hook", "delete_state_file",
            "emit_final_report", "emit_iteration_report", "none",
        }
        self.assertEqual(ALLOWED_DIRECTIVE_ACTIONS, expected)

    def test_preferred_model_strong_reasoner(self) -> None:
        self.assertEqual(PREFERRED_MODEL_BY_CLASS["strong_reasoner"], "claude-opus-4.6")

    def test_preferred_model_strong_coder(self) -> None:
        self.assertEqual(PREFERRED_MODEL_BY_CLASS["strong_coder"], "claude-opus-4.6")

    def test_preferred_model_general_worker(self) -> None:
        self.assertEqual(PREFERRED_MODEL_BY_CLASS["general_worker"], "claude-sonnet-4")

    def test_all_model_classes_have_preferred_model(self) -> None:
        expected_classes = {"general_worker", "strong_reasoner", "strong_coder"}
        self.assertEqual(set(PREFERRED_MODEL_BY_CLASS.keys()), expected_classes)

    def test_model_resolution_order_starts_with_preferred_model(self) -> None:
        for model_class, resolution_order in MODEL_RESOLUTION_ORDER_BY_CLASS.items():
            self.assertTrue(resolution_order)
            self.assertEqual(PREFERRED_MODEL_BY_CLASS[model_class], resolution_order[0])

    def test_background_modes_do_not_include_materialization(self) -> None:
        self.assertNotIn("materialization", ALLOWED_SLOT_MODES["background"])

    def test_auxiliary_slot_does_not_include_rollover_mode(self) -> None:
        """Rollover is inline dispatch — it must not be dispatched as an auxiliary slot.

        Allowing 'rollover' in ALLOWED_SLOT_MODES['auxiliary'] would let a control
        agent erroneously dispatch a slot-based rollover, bypassing the inline
        contract.  The guardrail must reject it.
        """
        self.assertNotIn(
            "rollover",
            ALLOWED_SLOT_MODES["auxiliary"],
            "rollover is inline dispatch and must not appear as an auxiliary slot mode",
        )


class ValidateExecutorPolicyV4Tests(unittest.TestCase):

    def test_launch_sweep_passes(self) -> None:
        directives = [{
            "action": "launch_sweep",
            "reason": "launch WandB sweep on Vast.ai",
            "sweep_config": {"method": "bayes", "parameters": {}},
            "sky_task_spec": {"accelerator": "A100:1"},
            "result_file": ".ml-metaopt/worker-results/launch-sweep-iter-2.json",
        }]
        result = validate_executor_policy("metaopt-remote-execution-control", "LAUNCH_SWEEP", directives)
        self.assertEqual(len(result), 1)

    def test_poll_sweep_passes(self) -> None:
        directives = [{
            "action": "poll_sweep",
            "reason": "check sweep status and watchdog",
            "sweep_id": "wandb-sweep-abc123",
            "sky_job_ids": ["sky-job-001"],
            "result_file": ".ml-metaopt/worker-results/poll-sweep-iter-2.json",
        }]
        result = validate_executor_policy("metaopt-remote-execution-control", "WAIT_FOR_SWEEP", directives)
        self.assertEqual(len(result), 1)

    def test_run_smoke_test_passes(self) -> None:
        directives = [{
            "action": "run_smoke_test",
            "reason": "60s crash-detection gate before GPU spend",
            "command": "python train.py --smoke",
            "result_file": ".ml-metaopt/worker-results/smoke-test-iter-2.json",
        }]
        result = validate_executor_policy("metaopt-remote-execution-control", "LOCAL_SANITY", directives)
        self.assertEqual(len(result), 1)

    def test_queue_op_blocked(self) -> None:
        directives = [{"action": "queue_op", "reason": "v3 compat"}]
        with self.assertRaises(ValueError):
            validate_executor_policy("any-agent", "SOME_PHASE", directives)

    def test_apply_patch_artifacts_blocked(self) -> None:
        directives = [{"action": "apply_patch_artifacts", "reason": "v3 compat"}]
        with self.assertRaises(ValueError):
            validate_executor_policy("any-agent", "SOME_PHASE", directives)

    def test_skypilot_worker_rejected_in_slot_launch_requests(self) -> None:
        request = {
            "worker_ref": "skypilot-wandb-worker",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/sky.md",
            "result_file": ".ml-metaopt/worker-results/sky.json",
        }
        with self.assertRaises(ValueError, msg="skypilot-wandb-worker must not appear in launch_requests"):
            normalize_launch_requests([request])

    def test_analysis_worker_in_auxiliary_slot_accepted(self) -> None:
        request = {
            "slot_id": "aux-1",
            "slot_class": "auxiliary",
            "mode": "analysis",
            "worker_ref": "metaopt-analysis-worker",
            "model_class": "strong_reasoner",
            "task_file": ".ml-metaopt/tasks/analysis-iter-2.md",
            "result_file": ".ml-metaopt/worker-results/analysis-iter-2.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["preferred_model"], "claude-opus-4.6")


if __name__ == "__main__":
    unittest.main()

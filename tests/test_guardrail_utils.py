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

    def test_background_slot_accepts_maintenance_mode(self) -> None:
        request = {
            "slot_id": "bg-1",
            "slot_class": "background",
            "mode": "maintenance",
            "worker_kind": "skill",
            "worker_ref": "repo-audit-refactor-optimize",
            "model_class": "general_worker",
            "task_file": ".ml-metaopt/tasks/bg-1.md",
            "result_file": ".ml-metaopt/worker-results/bg-1.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(len(result), 1)

    def test_strong_reasoner_gets_preferred_model(self) -> None:
        request = {
            "slot_id": "sel-1",
            "slot_class": "auxiliary",
            "mode": "selection",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-selection-worker",
            "model_class": "strong_reasoner",
            "task_file": ".ml-metaopt/tasks/selection.md",
            "result_file": ".ml-metaopt/worker-results/selection.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(result[0]["preferred_model"], "claude-opus-4.6")

    def test_strong_coder_gets_preferred_model(self) -> None:
        request = {
            "slot_id": "mat-1",
            "slot_class": "auxiliary",
            "mode": "materialization",
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-materialization-worker",
            "model_class": "strong_coder",
            "task_file": ".ml-metaopt/tasks/materialization.md",
            "result_file": ".ml-metaopt/worker-results/materialization.json",
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
            "worker_ref": "metaopt-selection-worker",
            "model_class": "strong_reasoner",
            "mode": "selection",
            "task_file": ".ml-metaopt/tasks/selection.md",
            "result_file": ".ml-metaopt/worker-results/selection.json",
        }
        with self.assertRaisesRegex(ValueError, "slot_class is required"):
            normalize_launch_requests([request])

    def test_slot_class_requires_mode(self) -> None:
        request = {
            "worker_ref": "metaopt-selection-worker",
            "model_class": "strong_reasoner",
            "slot_class": "auxiliary",
            "task_file": ".ml-metaopt/tasks/selection.md",
            "result_file": ".ml-metaopt/worker-results/selection.json",
        }
        with self.assertRaisesRegex(ValueError, "mode is required"):
            normalize_launch_requests([request])

    def test_worker_mode_mismatch_rejected(self) -> None:
        request = {
            "worker_ref": "metaopt-selection-worker",
            "model_class": "strong_reasoner",
            "slot_class": "auxiliary",
            "mode": "design",
            "task_file": ".ml-metaopt/tasks/selection.md",
            "result_file": ".ml-metaopt/worker-results/selection.json",
        }
        with self.assertRaisesRegex(ValueError, "requires one of"):
            normalize_launch_requests([request])

    def test_worker_model_class_mismatch_rejected(self) -> None:
        request = {
            "worker_ref": "metaopt-materialization-worker",
            "model_class": "strong_reasoner",
            "slot_class": "auxiliary",
            "mode": "materialization",
            "task_file": ".ml-metaopt/tasks/materialization.md",
            "result_file": ".ml-metaopt/worker-results/materialization.json",
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
                "action": "queue_op",
                "reason": "submit batch",
                "operation": "enqueue",
                "command": "enqueue batch",
                "batch_id": "batch-1",
                "result_file": ".ml-metaopt/queue-results/enqueue-batch-1.json",
            }
        ]
        result = validate_executor_policy("metaopt-remote-execution-control", "PLAN_REMOTE_BATCH", directives)
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
            "apply_patch_artifacts": {"result_file": ".ml-metaopt/worker-results/materialization.json", "target_worktree": ".ml-metaopt/worktrees/integration"},
            "cancel_slots": {"slot_ids": ["bg-1"]},
            "delete_state_file": {"state_path": ".ml-metaopt/state.json"},
            "drain_slots": {"drain_window_seconds": 60},
            "emit_final_report": {"report_type": "final"},
            "emit_iteration_report": {"report_type": "iteration", "iteration": 3},
            "package_code_artifact": {"worktree": ".ml-metaopt/worktrees/integration", "code_roots": ["src"], "output_event_path": ".ml-metaopt/executor-events/package-code.json"},
            "package_data_manifest": {"worktree": ".ml-metaopt/worktrees/integration", "data_roots": ["data"], "output_event_path": ".ml-metaopt/executor-events/package-data.json"},
            "queue_op": {"operation": "enqueue", "command": "enqueue batch", "batch_id": "batch-1", "result_file": ".ml-metaopt/queue-results/enqueue-batch-1.json"},
            "remove_agents_hook": {"agents_path": "AGENTS.md"},
            "run_sanity": {"worktree": ".ml-metaopt/worktrees/integration", "command": "pytest -q", "max_duration_seconds": 600, "output_event_path": ".ml-metaopt/executor-events/sanity.json"},
            "write_manifest": {"manifest_path": ".ml-metaopt/artifacts/manifests/batch.json", "batch_id": "batch-1"},
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
        directives = [{"action": "queue_op", "reason": "submit batch", "operation": "enqueue", "command": "enqueue batch", "batch_id": "batch-1"}]
        with self.assertRaisesRegex(ValueError, "result_file"):
            validate_executor_policy("metaopt-remote-execution-control", "PLAN_REMOTE_BATCH", directives)


class ConstantsSanityTests(unittest.TestCase):
    """Sanity checks for the exported constant sets."""

    def test_allowed_workers_contains_known_agents(self) -> None:
        expected = {
            "metaopt-ideation-worker",
            "metaopt-materialization-worker",
            "metaopt-diagnosis-worker",
            "metaopt-analysis-worker",
        }
        self.assertTrue(expected.issubset(ALLOWED_WORKERS))

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

    def test_rollover_launch_request_without_slot_class_is_accepted(self) -> None:
        """Rollover inline dispatch: a launch_requests entry without slot_class or mode
        must be accepted by normalize_launch_requests and receive preferred_model.
        """
        request = {
            "worker_ref": "metaopt-rollover-worker",
            "model_class": "strong_reasoner",
            "task_file": ".ml-metaopt/tasks/rollover-iter-1.md",
            "result_file": ".ml-metaopt/worker-results/rollover-iter-1.json",
        }
        result = normalize_launch_requests([request])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["preferred_model"], "claude-opus-4.6")
        self.assertNotIn("slot_class", result[0])
        self.assertNotIn("mode", result[0])

    def test_rollover_launch_request_with_auxiliary_slot_is_rejected(self) -> None:
        """Dispatching rollover as a slot-based auxiliary worker must be rejected.

        Only inline dispatch (no slot_class) is valid for rollover.
        """
        request = {
            "slot_class": "auxiliary",
            "mode": "rollover",
            "worker_ref": "metaopt-rollover-worker",
            "model_class": "strong_reasoner",
            "task_file": ".ml-metaopt/tasks/rollover-iter-1.md",
            "result_file": ".ml-metaopt/worker-results/rollover-iter-1.json",
        }
        with self.assertRaises(ValueError, msg="rollover must not be dispatched as an auxiliary slot"):
            normalize_launch_requests([request])


if __name__ == "__main__":
    unittest.main()

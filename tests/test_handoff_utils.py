from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts"))
from _handoff_utils import apply_state_patch, compute_state_patch, derive_status_for_machine_state, persist_state_handoff


class HandoffUtilsTests(unittest.TestCase):
    def test_compute_state_patch_ignores_machine_state_and_status_but_captures_owned_changes(self) -> None:
        before = {
            "status": "RUNNING",
            "machine_state": "SELECT_SWEEP",
            "next_action": "select sweep",
            "selected_sweep": None,
        }
        after = {
            "status": "RUNNING",
            "machine_state": "WAIT_FOR_SWEEP",
            "next_action": "launch sweep",
            "selected_sweep": {
                "sweep_id": "market-forecast-v3-s1",
                "selection_rationale": "best fit",
            },
        }

        patch = compute_state_patch(before, after, "metaopt-select-design")

        self.assertEqual(
            patch,
            {
                "next_action": "launch sweep",
                "selected_sweep": {
                    "sweep_id": "market-forecast-v3-s1",
                    "selection_rationale": "best fit",
                },
            },
        )

    def test_compute_state_patch_rejects_unauthorized_paths(self) -> None:
        before = {
            "status": "RUNNING",
            "machine_state": "WAIT_FOR_SWEEP",
            "next_action": "poll sweep status",
            "proposal_cycle": {"shortfall_reason": ""},
            "current_sweep": None,
        }
        after = deepcopy(before)
        after["proposal_cycle"]["shortfall_reason"] = "not_enough_proposals"

        with self.assertRaises(ValueError):
            compute_state_patch(before, after, "metaopt-remote-execution-control")

    def test_background_control_cannot_patch_selected_sweep(self) -> None:
        before = {
            "status": "RUNNING",
            "machine_state": "MAINTAIN_BACKGROUND_POOL",
            "next_action": "execute planned background work",
            "selected_sweep": None,
            "proposal_cycle": {"shortfall_reason": ""},
        }
        after = deepcopy(before)
        after["selected_sweep"] = {
            "sweep_id": "market-forecast-v3-s1",
            "selection_rationale": "unauthorized write",
        }

        with self.assertRaises(ValueError):
            compute_state_patch(before, after, "metaopt-background-control")

    def test_apply_state_patch_sets_machine_state_and_derives_status(self) -> None:
        before = {
            "status": "RUNNING",
            "machine_state": "WAIT_FOR_SWEEP",
            "next_action": "poll sweep status",
            "current_sweep": {"sweep_id": "s1", "status": "running"},
        }

        updated = apply_state_patch(
            before,
            {
                "next_action": "protocol violation: manual intervention required",
                "current_sweep": {"sweep_id": "s1", "status": "failed"},
            },
            "BLOCKED_PROTOCOL",
        )

        self.assertEqual(updated["machine_state"], "BLOCKED_PROTOCOL")
        self.assertEqual(updated["status"], "BLOCKED_PROTOCOL")
        self.assertEqual(updated["next_action"], "protocol violation: manual intervention required")
        self.assertEqual(updated["current_sweep"]["status"], "failed")

    def test_compute_state_patch_preserves_explicit_none_updates(self) -> None:
        before = {
            "status": "RUNNING",
            "machine_state": "ROLL_ITERATION",
            "current_proposals": [{"proposal_id": "market-forecast-v3-p1"}],
            "next_action": "roll iteration",
        }
        after = {
            "status": "RUNNING",
            "machine_state": "MAINTAIN_BACKGROUND_POOL",
            "current_proposals": None,
            "next_action": "maintain background pool",
        }

        patch = compute_state_patch(before, after, "metaopt-iteration-close-control")

        self.assertEqual(
            patch,
            {
                "current_proposals": None,
                "next_action": "maintain background pool",
            },
        )

    def test_derive_status_for_machine_state_marks_non_terminal_states_running(self) -> None:
        self.assertEqual(derive_status_for_machine_state("MAINTAIN_BACKGROUND_POOL"), "RUNNING")
        self.assertEqual(derive_status_for_machine_state("COMPLETE"), "COMPLETE")

    def test_persist_state_handoff_is_emit_only_by_default(self) -> None:
        before = {
            "status": "RUNNING",
            "machine_state": "SELECT_SWEEP",
            "next_action": "select sweep",
            "selected_sweep": None,
        }
        after = deepcopy(before)
        after["selected_sweep"] = {"sweep_id": "market-forecast-v3-s1"}
        payload = {"recommended_next_machine_state": "WAIT_FOR_SWEEP"}

        with tempfile.TemporaryDirectory() as tempdir_str:
            state_path = Path(tempdir_str) / "state.json"
            state_path.write_text(json.dumps(before), encoding="utf-8")
            old_env = os.environ.pop("METAOPT_APPLY_STATE_HANDOFF", None)
            try:
                persisted = persist_state_handoff(
                    state_path,
                    before,
                    after,
                    payload,
                    control_agent="metaopt-select-design",
                )
            finally:
                if old_env is not None:
                    os.environ["METAOPT_APPLY_STATE_HANDOFF"] = old_env

            self.assertFalse(payload["state_applied"])
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), before)
            self.assertEqual(persisted["machine_state"], "WAIT_FOR_SWEEP")


if __name__ == "__main__":
    unittest.main()

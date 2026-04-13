from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hydrate_state_handoff.py"
AGENT_PROFILE = ROOT / ".github" / "agents" / "metaopt-hydrate-state.agent.md"
AGENTS_HOOK = textwrap.dedent(
    """\
    <!-- ml-metaoptimization:begin -->
    ## Active task
    `.ml-metaopt/state.json` exists. Inspect its `status`.
    If `status` is `RUNNING`, invoke the `ml-metaoptimization` skill immediately,
    resume from `machine_state`, and do not ask the user for campaign inputs.
    If `status` is terminal, invoke the `ml-metaoptimization` skill once so terminal cleanup can run through control-agent directives; do not execute `next_action`.
    <!-- ml-metaoptimization:end -->
    """
)

_IDENTITY_HASH = "sha256:" + "a" * 64


def _v4_load_handoff(*, campaign_valid=True, campaign_id="test-campaign", identity_hash=_IDENTITY_HASH):
    return {
        "schema_version": 1,
        "handoff_type": "load_campaign.validate",
        "control_agent": "metaopt-load-campaign",
        "campaign_id": campaign_id if campaign_valid else None,
        "campaign_valid": campaign_valid,
        "campaign_identity_hash": identity_hash if campaign_valid else None,
        "objective_snapshot": {"metric": "val/accuracy", "direction": "maximize", "improvement_threshold": 0.005} if campaign_valid else None,
        "compute": {"provider": "vast_ai", "accelerator": "A100:1", "num_sweep_agents": 4, "idle_timeout_minutes": 15, "max_budget_usd": 10} if campaign_valid else None,
        "wandb": {"entity": "my-entity", "project": "my-project"} if campaign_valid else None,
        "project": {"repo": "git@github.com:org/repo.git", "smoke_test_command": "python train.py --smoke"} if campaign_valid else None,
        "proposal_policy": {"current_target": 5} if campaign_valid else None,
        "stop_conditions": {"max_iterations": 20, "target_metric": 0.99, "max_no_improve_iterations": 5} if campaign_valid else None,
        "validation_issues": [] if campaign_valid else ["campaign invalid"],
        "warnings": [],
        "recommended_next_machine_state": "HYDRATE_STATE" if campaign_valid else "BLOCKED_CONFIG",
        "recovery_action": None if campaign_valid else "repair ml_metaopt_campaign.yaml",
        "state_patch": None,
        "directives": [],
        "summary": "ok",
    }


def _v4_existing_state(*, identity_hash=_IDENTITY_HASH, status="RUNNING", machine_state="IDEATE", **overrides):
    base = {
        "version": 4,
        "campaign_id": "test-campaign",
        "campaign_identity_hash": identity_hash,
        "status": status,
        "machine_state": machine_state,
        "current_iteration": 1,
        "next_action": "maintain background pool",
        "objective_snapshot": {"metric": "val/accuracy", "direction": "maximize", "improvement_threshold": 0.005},
        "proposal_cycle": {"cycle_id": "iter-1-cycle-1", "current_pool_frozen": False},
        "current_sweep": None,
        "selected_sweep": None,
        "baseline": None,
        "current_proposals": [],
        "next_proposals": [],
        "key_learnings": [],
        "completed_iterations": [],
        "no_improve_iterations": 0,
        "campaign_started_at": "2026-04-13T07:00:00Z",
    }
    base.update(overrides)
    return base


class HydrateStateHandoffTests(unittest.TestCase):

    def _write_skills_manifest(self, tempdir, *, missing_required=False, missing_degradable=False):
        manifest_path = tempdir / "agents" / "worker-skills.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        required_probe = tempdir / ".github" / "agents" / "metaopt-ideation-worker.agent.md"
        if not missing_required:
            required_probe.parent.mkdir(parents=True, exist_ok=True)
            required_probe.write_text("ok\n", encoding="utf-8")

        degradable_probe = tempdir / "skills" / "repo-audit-refactor-optimize" / "SKILL.md"
        if not missing_degradable:
            degradable_probe.parent.mkdir(parents=True, exist_ok=True)
            degradable_probe.write_text("ok\n", encoding="utf-8")

        payload = {
            "skills": [
                {
                    "name": "metaopt-ideation-worker",
                    "lane": "ideation",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-ideation-worker",
                    "classification": "required",
                    "probe_paths": [str(required_probe)],
                },
                {
                    "name": "repo-audit-refactor-optimize",
                    "lane": "maintenance",
                    "worker_kind": "skill",
                    "worker_ref": "repo-audit-refactor-optimize",
                    "classification": "degradable",
                    "degraded_lane": "maintenance",
                    "probe_paths": [str(degradable_probe)],
                },
            ],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return manifest_path

    def _write_full_skills_manifest(self, tempdir):
        """Manifest with all probe_paths pointing to files that exist on disk."""
        manifest_path = tempdir / "agents" / "worker-skills.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        # Create probe stubs for workers that don't have real .agent.md on disk
        probes_dir = tempdir / ".github" / "agents"
        probes_dir.mkdir(parents=True, exist_ok=True)
        worker_names = [
            "metaopt-ideation-worker", "metaopt-selection-worker",
            "metaopt-design-worker", "metaopt-materialization-worker",
            "metaopt-diagnosis-worker", "metaopt-analysis-worker",
        ]
        for w in worker_names:
            probe = probes_dir / f"{w}.agent.md"
            if not probe.exists():
                probe.write_text("stub\n", encoding="utf-8")

        rollover_probe = probes_dir / "metaopt-rollover-worker.agent.md"
        if not rollover_probe.exists():
            rollover_probe.write_text("stub\n", encoding="utf-8")

        maint_probe = tempdir / "skills" / "repo-audit-refactor-optimize" / "SKILL.md"
        maint_probe.parent.mkdir(parents=True, exist_ok=True)
        maint_probe.write_text("stub\n", encoding="utf-8")

        payload = {
            "skills": [
                {"name": n, "lane": n.split("-")[-1] if n.startswith("metaopt-") else "maintenance", "worker_kind": "custom_agent", "worker_ref": n, "classification": "required", "probe_paths": [str(probes_dir / f"{n}.agent.md")]}
                for n in worker_names
            ] + [
                {"name": "metaopt-rollover-worker", "lane": "rollover", "worker_kind": "custom_agent", "worker_ref": "metaopt-rollover-worker", "classification": "degradable", "degraded_lane": "rollover", "probe_paths": [str(rollover_probe)]},
                {"name": "repo-audit-refactor-optimize", "lane": "maintenance", "worker_kind": "skill", "worker_ref": "repo-audit-refactor-optimize", "classification": "degradable", "degraded_lane": "maintenance", "probe_paths": [str(maint_probe)]},
            ],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return manifest_path

    def _run_tool(self, tempdir, *, campaign_valid=True, missing_required=False, missing_degradable=False, state_payload=None, agents_text=None, full_manifest=False):
        load_h_path = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        load_h_path.parent.mkdir(parents=True, exist_ok=True)
        load_h_path.write_text(json.dumps(_v4_load_handoff(campaign_valid=campaign_valid)), encoding="utf-8")

        if full_manifest:
            skills_manifest = self._write_full_skills_manifest(tempdir)
        else:
            skills_manifest = self._write_skills_manifest(tempdir, missing_required=missing_required, missing_degradable=missing_degradable)

        state_path = tempdir / ".ml-metaopt" / "state.json"
        if state_payload is not None:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(state_payload, str):
                state_path.write_text(state_payload, encoding="utf-8")
            else:
                state_path.write_text(json.dumps(state_payload), encoding="utf-8")

        agents_path = tempdir / "AGENTS.md"
        if agents_text is not None:
            agents_path.write_text(agents_text, encoding="utf-8")

        output_path = tempdir / ".ml-metaopt" / "handoffs" / "hydrate_state.latest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        r = subprocess.run(
            ["python3", str(SCRIPT), "--load-handoff", str(load_h_path),
             "--state-path", str(state_path), "--agents-path", str(agents_path),
             "--skills-manifest", str(skills_manifest), "--output", str(output_path),
             "--apply-state"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(r.returncode, 0, msg=f"stdout:\n{r.stdout}\n\nstderr:\n{r.stderr}")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, json.loads(r.stdout))
        return payload, state_path, agents_path

    # -- fresh init --

    def test_fresh_init_writes_running_state_and_hook(self):
        with tempfile.TemporaryDirectory() as td:
            payload, state_path, agents_path = self._run_tool(Path(td))
            self.assertEqual(payload["resume_mode"], "fresh")
            self.assertEqual(payload["effective_status"], "RUNNING")
            self.assertEqual(payload["effective_machine_state"], "IDEATE")
            self.assertTrue(payload["state_written"])
            self.assertEqual(payload["agents_hook_action"], "created")
            self.assertTrue(state_path.exists())
            self.assertEqual(agents_path.read_text(encoding="utf-8"), AGENTS_HOOK)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "RUNNING")
            self.assertEqual(state["machine_state"], "IDEATE")
            self.assertEqual(state["current_iteration"], 1)
            self.assertEqual(state["next_action"], "maintain background pool")
            self.assertEqual(state["current_proposals"], [])
            self.assertEqual(state["next_proposals"], [])

    def test_fresh_init_sets_campaign_started_at(self):
        with tempfile.TemporaryDirectory() as td:
            _, state_path, _ = self._run_tool(Path(td))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("campaign_started_at", state)

    def test_fresh_init_with_full_worker_manifest_records_all_required_targets(self):
        with tempfile.TemporaryDirectory() as td:
            payload, state_path, _ = self._run_tool(Path(td), full_manifest=True)
            self.assertEqual(payload["resume_mode"], "fresh")
            self.assertEqual(payload["effective_status"], "RUNNING")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "RUNNING")
            self.assertEqual(state["machine_state"], "IDEATE")

    # -- resume --

    def test_resume_matching_state_updates_runtime_capabilities_and_hook(self):
        with tempfile.TemporaryDirectory() as td:
            existing = _v4_existing_state()
            payload, state_path, agents_path = self._run_tool(
                Path(td), state_payload=existing, agents_text="# Notes\n",
            )
            self.assertEqual(payload["effective_status"], "RUNNING")
            self.assertEqual(payload["resume_mode"], "existing")
            self.assertEqual(payload["effective_machine_state"], "IDEATE")
            self.assertEqual(payload["agents_hook_action"], "updated")
            self.assertIn(AGENTS_HOOK, agents_path.read_text(encoding="utf-8"))

    def test_resume_preserves_campaign_started_at(self):
        with tempfile.TemporaryDirectory() as td:
            existing = _v4_existing_state(campaign_started_at="2026-01-01T00:00:00Z")
            _, state_path, _ = self._run_tool(Path(td), state_payload=existing)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["campaign_started_at"], "2026-01-01T00:00:00Z")

    def test_resume_defaults_campaign_started_at_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            existing = _v4_existing_state()
            del existing["campaign_started_at"]
            _, state_path, _ = self._run_tool(Path(td), state_payload=existing)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("campaign_started_at", state)

    def test_resume_blocked_protocol_state_removes_hook_and_reports_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            existing = _v4_existing_state(status="BLOCKED_PROTOCOL", machine_state="BLOCKED_PROTOCOL")
            payload, _, _ = self._run_tool(
                Path(td), state_payload=existing, agents_text=AGENTS_HOOK,
            )
            self.assertEqual(payload["effective_status"], "BLOCKED_PROTOCOL")
            self.assertEqual(payload["agents_hook_action"], "remove_directive_emitted")
            self.assertEqual(len(payload["directives"]), 1)
            self.assertEqual(payload["directives"][0]["action"], "remove_agents_hook")

    # -- identity mismatch --

    def test_identity_mismatch_preserves_state_and_removes_hook(self):
        with tempfile.TemporaryDirectory() as td:
            stale = _v4_existing_state(identity_hash="sha256:" + "0" * 64)
            original = json.dumps(stale, sort_keys=True)
            payload, state_path, agents_path = self._run_tool(
                Path(td), state_payload=stale, agents_text=AGENTS_HOOK + "\nOther notes\n",
            )
            self.assertEqual(payload["effective_status"], "BLOCKED_CONFIG")
            self.assertFalse(payload["state_written"])
            self.assertTrue(payload["state_preserved"])
            self.assertEqual(payload["agents_hook_action"], "remove_directive_emitted")
            self.assertEqual([d["action"] for d in payload["directives"]], ["remove_agents_hook"])
            self.assertEqual(payload["recovery_action"], "archive or remove the stale state before starting a new campaign")
            self.assertEqual(json.dumps(json.loads(state_path.read_text(encoding="utf-8")), sort_keys=True), original)

    def test_identity_mismatch_contains_control_protocol_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            stale = _v4_existing_state(identity_hash="sha256:" + "0" * 64)
            payload, _, _ = self._run_tool(Path(td), state_payload=stale)
            self.assertEqual(payload["handoff_type"], "hydrate_state.hydrate")
            self.assertEqual(payload["control_agent"], "metaopt-hydrate-state")
            self.assertIn("state_patch", payload)
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)
            self.assertIn("directives", payload)

    # -- missing skills --

    def test_missing_required_skill_writes_blocked_state(self):
        with tempfile.TemporaryDirectory() as td:
            payload, state_path, agents_path = self._run_tool(
                Path(td), missing_required=True, agents_text=AGENTS_HOOK,
            )
            self.assertEqual(payload["resume_mode"], "fresh")
            self.assertEqual(payload["effective_status"], "BLOCKED_CONFIG")
            self.assertEqual(payload["effective_machine_state"], "BLOCKED_CONFIG")
            self.assertTrue(payload["state_written"])
            self.assertEqual(payload["agents_hook_action"], "remove_directive_emitted")
            self.assertEqual([d["action"] for d in payload["directives"]], ["remove_agents_hook"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "BLOCKED_CONFIG")
            self.assertEqual(state["machine_state"], "BLOCKED_CONFIG")
            self.assertEqual(state["next_action"], "install missing skill: metaopt-ideation-worker")

    def test_missing_required_skill_on_resumed_state_preserves_campaign_data(self):
        with tempfile.TemporaryDirectory() as td:
            existing = _v4_existing_state(
                current_iteration=5,
                key_learnings=["feature X showed no improvement"],
                current_proposals=[{"proposal_id": "test-campaign-p12", "title": "Try feature X"}],
                baseline={"metric": "val/accuracy", "value": 0.95, "wandb_run_id": "r", "wandb_run_url": "u", "established_at": "t"},
            )
            payload, state_path, _ = self._run_tool(
                Path(td), state_payload=existing, missing_required=True, agents_text=AGENTS_HOOK,
            )
            self.assertEqual(payload["resume_mode"], "existing")
            self.assertEqual(payload["effective_status"], "BLOCKED_CONFIG")
            self.assertTrue(payload["state_written"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "BLOCKED_CONFIG")
            self.assertEqual(state["current_iteration"], 5)
            self.assertEqual(state["key_learnings"], ["feature X showed no improvement"])
            self.assertEqual(state["current_proposals"][0]["proposal_id"], "test-campaign-p12")
            self.assertEqual(state["baseline"]["value"], 0.95)

    def test_missing_degradable_skill_continues_with_degraded_lane(self):
        with tempfile.TemporaryDirectory() as td:
            payload, state_path, _ = self._run_tool(Path(td), missing_degradable=True)
            self.assertEqual(payload["effective_status"], "RUNNING")
            self.assertEqual(payload["effective_machine_state"], "IDEATE")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "RUNNING")

    def test_terminal_state_preserved_when_blocking_skill_also_present(self):
        with tempfile.TemporaryDirectory() as td:
            existing = _v4_existing_state(status="COMPLETE", machine_state="COMPLETE")
            payload, _, _ = self._run_tool(
                Path(td), state_payload=existing, missing_required=True,
            )
            self.assertEqual(payload["effective_status"], "COMPLETE")

    # -- envelope keys --

    def test_fresh_init_contains_control_protocol_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            payload, _, _ = self._run_tool(Path(td))
            self.assertEqual(payload["handoff_type"], "hydrate_state.hydrate")
            self.assertEqual(payload["control_agent"], "metaopt-hydrate-state")
            self.assertIn("state_patch", payload)
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)
            self.assertIn("directives", payload)

    def test_runtime_error_contains_control_protocol_envelope_keys(self):
        with tempfile.TemporaryDirectory() as td:
            payload, _, _ = self._run_tool(Path(td), campaign_valid=False)
            self.assertEqual(payload["handoff_type"], "hydrate_state.hydrate")
            self.assertEqual(payload["control_agent"], "metaopt-hydrate-state")
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertIn("state_patch", payload)
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    # -- agent profile --

    def test_agent_profile_exists_and_is_programmatic_only(self):
        self.assertTrue(AGENT_PROFILE.exists(), f"missing {AGENT_PROFILE}")
        content = AGENT_PROFILE.read_text(encoding="utf-8")
        self.assertIn("name: metaopt-hydrate-state", content)
        self.assertIn("model:", content)
        self.assertIn("description:", content)
        self.assertIn("tools:", content)
        self.assertIn("user-invocable: false", content)
        self.assertIn("scripts/hydrate_state_handoff.py", content)

    def test_invalid_load_handoff_returns_runtime_error_without_state_write(self):
        with tempfile.TemporaryDirectory() as td:
            payload, state_path, _ = self._run_tool(Path(td), campaign_valid=False)
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertFalse(state_path.exists())

    def test_malformed_existing_state_returns_runtime_error_without_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            payload, state_path, _ = self._run_tool(
                Path(td), state_payload="this is not json",
            )
            self.assertIsNone(payload["recommended_next_machine_state"])
            self.assertEqual(state_path.read_text(encoding="utf-8"), "this is not json")


if __name__ == "__main__":
    unittest.main()

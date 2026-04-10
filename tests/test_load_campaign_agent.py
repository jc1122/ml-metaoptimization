from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "load_campaign_handoff.py"
AGENT_PROFILE = ROOT / ".github" / "agents" / "metaopt-load-campaign.agent.md"


class LoadCampaignHandoffTests(unittest.TestCase):
    def _run_tool(self, tempdir: Path, *, campaign_text: str, state_text: str | None = None) -> dict:
        campaign_path = tempdir / "ml_metaopt_campaign.yaml"
        campaign_path.write_text(campaign_text, encoding="utf-8")

        state_path = tempdir / ".ml-metaopt" / "state.json"
        if state_text is not None:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(state_text, encoding="utf-8")

        output_path = tempdir / ".ml-metaopt" / "handoffs" / "load_campaign.latest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        completed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--campaign-path",
                str(campaign_path),
                "--state-path",
                str(state_path),
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
        return payload

    def test_valid_campaign_emits_ok_handoff_with_matching_state_peek(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            state = json.dumps(
                {
                    "campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c"
                }
            )
            self._make_preflight_artifact(tempdir)

            payload = self._run_tool(tempdir, campaign_text=campaign, state_text=state)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertTrue(payload["campaign_valid"])
            self.assertEqual(payload["campaign_id"], "market-forecast-v3")
            self.assertEqual(payload["recommended_next_machine_state"], "HYDRATE_STATE")
            self.assertIsNone(payload["recovery_action"])
            self.assertEqual(payload["goal"], "Improve out-of-sample forecast quality without temporal leakage.")
            self.assertIsInstance(payload["stop_conditions"], dict)
            self.assertIsInstance(payload["datasets"], list)
            self.assertIsInstance(payload["sanity"], dict)
            self.assertIsInstance(payload["artifacts"], dict)
            self.assertIsInstance(payload["remote_queue"], dict)
            self.assertIsInstance(payload["execution"], dict)
            self.assertEqual(payload["validation_issues"], [])
            self.assertEqual(payload["state_peek"]["identity_relation"], "match")
            self.assertEqual(payload["state_peek"]["campaign_identity_hash"], payload["campaign_identity_hash"])

    def test_invalid_campaign_blocks_config_and_reports_sentinel_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            invalid_campaign = (
                "version: 3\n"
                "campaign_id: market-forecast-v3\n"
                "goal: Improve out-of-sample forecast quality without temporal leakage.\n"
                "objective:\n"
                "  metric: rmse\n"
                "  direction: minimize\n"
                "  aggregation:\n"
                "    method: weighted_mean\n"
                "  improvement_threshold: 0.0005\n"
                "datasets:\n"
                "  - id: ds_main\n"
                "    local_path: <replace-me>\n"
                "    role: train_eval\n"
                "    fingerprint: sha256:replace-me\n"
                "baseline:\n"
                "  aggregate: 0.1284\n"
                "  by_dataset:\n"
                "    ds_main: 0.1269\n"
                "stop_conditions:\n"
                "  max_wallclock_hours: 72\n"
                "proposal_policy:\n"
                "  current_target: 8\n"
                "dispatch_policy:\n"
                "  background_slots: 8\n"
                "  auxiliary_slots: 2\n"
                "sanity:\n"
                "  command: YOUR_SANITY_COMMAND\n"
                "artifacts:\n"
                "  code_roots:\n"
                "    - .\n"
                "remote_queue:\n"
                "  backend: ray-hetzner\n"
                "  retry_policy:\n"
                "    max_attempts: 2\n"
                "  enqueue_command: python3 /opt/ray-hetzner/metaopt/enqueue_batch.py --manifest\n"
                "  status_command: python3 /opt/ray-hetzner/metaopt/get_batch_status.py --batch-id\n"
                "  results_command: python3 /opt/ray-hetzner/metaopt/fetch_batch_results.py --batch-id\n"
                "execution:\n"
                "  entrypoint: python3 /srv/metaopt/project/scripts/ray_runner.py\n"
            )

            payload = self._run_tool(tempdir, campaign_text=invalid_campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertFalse(payload["campaign_valid"])
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            self.assertEqual(payload["recovery_action"], "repair ml_metaopt_campaign.yaml")
            self.assertGreaterEqual(len(payload["validation_issues"]), 3)
            self.assertIn("state file not found", payload["warnings"])
            joined_issues = " ".join(payload["validation_issues"])
            self.assertIn("sentinel placeholder", joined_issues)

    def test_state_peek_mismatch_is_advisory_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            state = json.dumps({"campaign_identity_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000"})
            self._make_preflight_artifact(tempdir)

            payload = self._run_tool(tempdir, campaign_text=campaign, state_text=state)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["state_peek"]["identity_relation"], "mismatch")
            self.assertEqual(payload["recommended_next_machine_state"], "HYDRATE_STATE")
            self.assertIsNone(payload["recovery_action"])
            self.assertIn("state identity mismatch detected", payload["warnings"])

    def test_valid_handoff_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            state = json.dumps(
                {"campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c"}
            )
            self._make_preflight_artifact(tempdir)
            payload = self._run_tool(tempdir, campaign_text=campaign, state_text=state)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["control_agent"], "metaopt-load-campaign")
            self.assertEqual(payload["launch_requests"], [])
            self.assertIsNone(payload["state_patch"])
            self.assertEqual(payload["executor_directives"], [])
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_blocked_handoff_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            invalid_campaign = "version: 2\ncampaign_id: bad\n"
            payload = self._run_tool(tempdir, campaign_text=invalid_campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["control_agent"], "metaopt-load-campaign")
            self.assertEqual(payload["launch_requests"], [])
            self.assertIsNone(payload["state_patch"])
            self.assertEqual(payload["executor_directives"], [])

    def test_agent_profile_exists_and_is_programmatic_only(self) -> None:
        self.assertTrue(AGENT_PROFILE.exists(), f"missing {AGENT_PROFILE}")
        content = AGENT_PROFILE.read_text(encoding="utf-8")

        self.assertIn("name: metaopt-load-campaign", content)
        self.assertIn("model:", content)
        self.assertIn("description:", content)
        self.assertIn("tools:", content)
        self.assertIn("user-invocable: false", content)
        self.assertIn("scripts/load_campaign_handoff.py", content)


    # ------------------------------------------------------------------ #
    # Preflight readiness gate tests
    # ------------------------------------------------------------------ #

    EXAMPLE_IDENTITY_HASH = "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c"
    EXAMPLE_RUNTIME_HASH = "sha256:6f59ca57fb3da56f815d7fb03f8be7335fa9d14344c49154308e9e65990e9ac6"

    def _make_preflight_artifact(
        self,
        tempdir: Path,
        *,
        status: str = "READY",
        schema_version: int = 1,
        campaign_identity_hash: str | None = None,
        runtime_config_hash: str | None = None,
        next_action: str = "proceed",
        failures: list | None = None,
    ) -> Path:
        artifact = {
            "schema_version": schema_version,
            "status": status,
            "campaign_id": "market-forecast-v3",
            "campaign_identity_hash": campaign_identity_hash or self.EXAMPLE_IDENTITY_HASH,
            "runtime_config_hash": runtime_config_hash or self.EXAMPLE_RUNTIME_HASH,
            "emitted_at": "2025-01-01T00:00:00Z",
            "preflight_duration_seconds": 1.5,
            "checks_summary": {"total": 3, "passed": 3, "failed": 0, "bootstrapped": 0},
            "failures": failures or [],
            "next_action": next_action,
            "diagnostics": None,
        }
        path = tempdir / ".ml-metaopt" / "preflight-readiness.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact), encoding="utf-8")
        return path

    def test_valid_campaign_with_fresh_ready_preflight_proceeds_to_hydrate_state(self) -> None:
        """Valid campaign + fresh READY preflight artifact -> HYDRATE_STATE."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            state = json.dumps({"campaign_identity_hash": self.EXAMPLE_IDENTITY_HASH})
            self._make_preflight_artifact(tempdir)

            payload = self._run_tool(tempdir, campaign_text=campaign, state_text=state)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["recommended_next_machine_state"], "HYDRATE_STATE")
            self.assertTrue(payload["campaign_valid"])
            # Advisory preflight_readiness peek must be present and inspectable
            pr = payload["preflight_readiness"]
            self.assertEqual(pr["status"], "fresh_ready")
            self.assertTrue(pr["exists"])
            self.assertTrue(pr["binding_fresh"])

    def test_valid_campaign_missing_preflight_blocks_config(self) -> None:
        """Valid campaign + missing preflight artifact -> BLOCKED_CONFIG with run-preflight action."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")

            payload = self._run_tool(tempdir, campaign_text=campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            self.assertIn("metaopt-preflight", payload["recovery_action"])
            pr = payload["preflight_readiness"]
            self.assertFalse(pr["exists"])
            self.assertFalse(pr["binding_fresh"])
            self.assertEqual(pr["status"], "missing")

    def test_valid_campaign_stale_preflight_blocks_config(self) -> None:
        """Valid campaign + stale preflight artifact (hash mismatch) -> BLOCKED_CONFIG with rerun action."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            # Write a preflight artifact with a mismatched identity hash
            self._make_preflight_artifact(
                tempdir,
                campaign_identity_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            )

            payload = self._run_tool(tempdir, campaign_text=campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            self.assertIn("metaopt-preflight", payload["recovery_action"])
            pr = payload["preflight_readiness"]
            self.assertTrue(pr["exists"])
            self.assertFalse(pr["binding_fresh"])
            self.assertEqual(pr["status"], "stale")

    def test_valid_campaign_stale_preflight_unrecognized_schema_blocks(self) -> None:
        """Valid campaign + preflight with unrecognized schema_version -> BLOCKED_CONFIG."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            self._make_preflight_artifact(tempdir, schema_version=999)

            payload = self._run_tool(tempdir, campaign_text=campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            self.assertIn("metaopt-preflight", payload["recovery_action"])
            pr = payload["preflight_readiness"]
            self.assertTrue(pr["exists"])
            self.assertFalse(pr["binding_fresh"])
            self.assertEqual(pr["status"], "stale")

    def test_valid_campaign_fresh_failed_preflight_blocks_with_remediation(self) -> None:
        """Valid campaign + fresh FAILED preflight artifact -> BLOCKED_CONFIG with artifact remediation."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            self._make_preflight_artifact(
                tempdir,
                status="FAILED",
                next_action="fix backend connectivity and re-run metaopt-preflight",
                failures=[
                    {
                        "check_id": "backend_reachability",
                        "category": "backend",
                        "message": "Backend unreachable",
                        "remediation": "Check network connectivity",
                    }
                ],
            )

            payload = self._run_tool(tempdir, campaign_text=campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            # Should surface the artifact's next_action, not a generic rerun message
            self.assertIn("fix backend connectivity", payload["recovery_action"])
            pr = payload["preflight_readiness"]
            self.assertTrue(pr["exists"])
            self.assertTrue(pr["binding_fresh"])
            self.assertEqual(pr["status"], "fresh_failed")
            self.assertEqual(len(pr["failures"]), 1)

    def test_valid_campaign_unknown_preflight_status_blocks_as_stale(self) -> None:
        """Preflight artifact with an unrecognized status string should block as stale/invalid."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            self._make_preflight_artifact(tempdir, status="INVALID_STATUS")

            payload = self._run_tool(tempdir, campaign_text=campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            pr = payload["preflight_readiness"]
            self.assertTrue(pr["exists"])
            self.assertEqual(pr["status"], "stale")

    def test_invalid_campaign_preflight_exists_reflects_file_presence(self) -> None:
        """When campaign validation fails and preflight is not evaluated,
        preflight_readiness.exists must reflect whether the file actually exists."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            invalid_campaign = "version: 2\ncampaign_id: bad\n"
            self._make_preflight_artifact(tempdir)

            payload = self._run_tool(tempdir, campaign_text=invalid_campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            pr = payload["preflight_readiness"]
            self.assertEqual(pr["status"], "not_evaluated")
            self.assertTrue(
                pr["exists"],
                "preflight_readiness.exists must be True when the artifact file is on disk",
            )

    def test_invalid_campaign_blocks_before_preflight_check(self) -> None:
        """Invalid campaign YAML must still block for campaign reasons first, ignoring preflight."""
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            invalid_campaign = "version: 2\ncampaign_id: bad\n"
            # Even with a valid preflight artifact, campaign validation fails first
            self._make_preflight_artifact(tempdir)

            payload = self._run_tool(tempdir, campaign_text=invalid_campaign)

            self.assertEqual(payload["handoff_type"], "load_campaign.validate")
            self.assertFalse(payload["campaign_valid"])
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            self.assertEqual(payload["recovery_action"], "repair ml_metaopt_campaign.yaml")
            # preflight_readiness should still be present but show not_evaluated or similar
            pr = payload["preflight_readiness"]
            self.assertEqual(pr["status"], "not_evaluated")


if __name__ == "__main__":
    unittest.main()

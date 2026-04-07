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

            payload = self._run_tool(tempdir, campaign_text=campaign, state_text=state)

            self.assertEqual(payload["phase"], "LOAD_CAMPAIGN")
            self.assertEqual(payload["outcome"], "ok")
            self.assertTrue(payload["campaign_valid"])
            self.assertEqual(payload["campaign_id"], "market-forecast-v3")
            self.assertEqual(payload["recommended_next_machine_state"], "HYDRATE_STATE")
            self.assertEqual(payload["recommended_next_action"], "hydrate or initialize orchestrator state")
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

            self.assertEqual(payload["outcome"], "blocked_config")
            self.assertFalse(payload["campaign_valid"])
            self.assertEqual(payload["recommended_next_machine_state"], "BLOCKED_CONFIG")
            self.assertEqual(payload["recommended_next_action"], "repair ml_metaopt_campaign.yaml")
            self.assertGreaterEqual(len(payload["validation_issues"]), 3)
            self.assertIn("state file not found", payload["warnings"])
            joined_issues = " ".join(payload["validation_issues"])
            self.assertIn("sentinel placeholder", joined_issues)

    def test_state_peek_mismatch_is_advisory_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            state = json.dumps({"campaign_identity_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000"})

            payload = self._run_tool(tempdir, campaign_text=campaign, state_text=state)

            self.assertEqual(payload["outcome"], "ok")
            self.assertEqual(payload["state_peek"]["identity_relation"], "mismatch")
            self.assertEqual(payload["recommended_next_machine_state"], "HYDRATE_STATE")
            self.assertIn("state identity mismatch detected", payload["warnings"])

    def test_valid_handoff_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            campaign = (ROOT / "ml_metaopt_campaign.example.yaml").read_text(encoding="utf-8")
            state = json.dumps(
                {"campaign_identity_hash": "sha256:f50928628873800b25a5dfb41f2fd6c93acfc210424953f53a5005e09379fa4c"}
            )
            payload = self._run_tool(tempdir, campaign_text=campaign, state_text=state)

            self.assertEqual(payload["handoff_type"], "LOAD_CAMPAIGN")
            self.assertEqual(payload["control_agent"], "metaopt-load-campaign")
            self.assertEqual(payload["launch_requests"], [])
            self.assertEqual(payload["state_patch"], {})
            self.assertEqual(payload["executor_directives"], [])
            self.assertIn("summary", payload)
            self.assertIn("warnings", payload)

    def test_blocked_handoff_contains_control_protocol_envelope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir_str:
            tempdir = Path(tempdir_str)
            invalid_campaign = "version: 2\ncampaign_id: bad\n"
            payload = self._run_tool(tempdir, campaign_text=invalid_campaign)

            self.assertEqual(payload["handoff_type"], "LOAD_CAMPAIGN")
            self.assertEqual(payload["control_agent"], "metaopt-load-campaign")
            self.assertEqual(payload["launch_requests"], [])
            self.assertEqual(payload["state_patch"], {})
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


if __name__ == "__main__":
    unittest.main()

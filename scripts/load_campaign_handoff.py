from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _handoff_utils import emit_handoff


REQUIRED_TOP_LEVEL_FIELDS = (
    "version",
    "campaign_id",
    "goal",
    "objective",
    "datasets",
    "baseline",
    "stop_conditions",
    "proposal_policy",
    "dispatch_policy",
    "sanity",
    "artifacts",
    "remote_queue",
    "execution",
)
REQUIRED_NESTED_FIELDS = (
    ("objective", "metric"),
    ("objective", "direction"),
    ("objective", "aggregation"),
    ("objective", "improvement_threshold"),
    ("baseline", "aggregate"),
    ("baseline", "by_dataset"),
    ("stop_conditions", "max_wallclock_hours"),
    ("proposal_policy", "current_target"),
    ("dispatch_policy", "background_slots"),
    ("dispatch_policy", "auxiliary_slots"),
    ("sanity", "command"),
    ("artifacts", "code_roots"),
    ("remote_queue", "backend"),
    ("remote_queue", "retry_policy"),
    ("execution", "entrypoint"),
)
REQUIRED_REMOTE_QUEUE_COMMANDS = ("enqueue_command", "status_command", "results_command")
IDENTITY_DATASET_FIELDS = ("id", "role", "fingerprint")
SENTINEL_SUBSTRINGS = ("YOUR_", "replace-me")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an advisory LOAD_CAMPAIGN handoff for the orchestrator.")
    parser.add_argument("--campaign-path", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256(payload: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def _contains_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        return any(token in value for token in SENTINEL_SUBSTRINGS) or ("<" in value and ">" in value)
    if isinstance(value, list):
        return any(_contains_sentinel(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_sentinel(item) for item in value.values())
    return False


def _get_nested(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _validate_campaign(campaign: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    if not isinstance(campaign, dict):
        return ["campaign root must be a mapping"]

    version = campaign.get("version")
    if version != 3:
        issues.append("version must be 3")

    for key in REQUIRED_TOP_LEVEL_FIELDS:
        if key not in campaign:
            issues.append(f"missing required field: {key}")

    for path in REQUIRED_NESTED_FIELDS:
        if _get_nested(campaign, path) is None:
            issues.append(f"missing required field: {'.'.join(path)}")

    datasets = campaign.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        issues.append("datasets must be a non-empty list")
    else:
        for index, dataset in enumerate(datasets):
            if not isinstance(dataset, dict):
                issues.append(f"datasets[{index}] must be an object")
                continue
            for field in ("id", "local_path", "role", "fingerprint"):
                if not dataset.get(field):
                    issues.append(f"datasets[{index}].{field} is required")
            fingerprint = dataset.get("fingerprint")
            if isinstance(fingerprint, str):
                if "replace-me" in fingerprint:
                    issues.append(f"datasets[{index}].fingerprint contains a sentinel placeholder")
                elif not (fingerprint.startswith("sha256:") and len(fingerprint) == 71):
                    issues.append(f"datasets[{index}].fingerprint must be a sha256 digest")

    for path in (
        ("sanity", "command"),
        ("execution", "entrypoint"),
        ("remote_queue", "enqueue_command"),
        ("remote_queue", "status_command"),
        ("remote_queue", "results_command"),
    ):
        value = _get_nested(campaign, path)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            issues.append(f"{'.'.join(path)} must be a non-empty string")
        elif _contains_sentinel(value):
            issues.append(f"{'.'.join(path)} contains a sentinel placeholder")

    for command_name in REQUIRED_REMOTE_QUEUE_COMMANDS:
        if _get_nested(campaign, ("remote_queue", command_name)) is None:
            issues.append(f"missing required field: remote_queue.{command_name}")

    if _contains_sentinel(campaign.get("artifacts")):
        issues.append("artifacts contains a sentinel placeholder")
    if _contains_sentinel(campaign.get("datasets")):
        issues.append("datasets contains a sentinel placeholder")

    return issues


def _identity_hash(campaign: dict[str, Any]) -> str:
    datasets = campaign.get("datasets", [])
    dataset_view = []
    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        dataset_view.append({field: dataset.get(field) for field in IDENTITY_DATASET_FIELDS})
    dataset_view.sort(key=lambda item: json.dumps(item, sort_keys=True))
    payload = {
        "version": campaign.get("version"),
        "campaign_id": campaign.get("campaign_id"),
        "objective": {
            "metric": _get_nested(campaign, ("objective", "metric")),
            "direction": _get_nested(campaign, ("objective", "direction")),
            "aggregation": _get_nested(campaign, ("objective", "aggregation")),
        },
        "datasets": dataset_view,
    }
    return _sha256(payload)


def _runtime_hash(campaign: dict[str, Any]) -> str:
    payload = {
        "sanity": campaign.get("sanity"),
        "artifacts": campaign.get("artifacts"),
        "remote_queue": campaign.get("remote_queue"),
        "execution": campaign.get("execution"),
    }
    return _sha256(payload)


def _peek_state(state_path: Path, *, campaign_identity_hash: str | None) -> tuple[dict[str, Any], list[str]]:
    state_peek: dict[str, Any] = {
        "path": str(state_path),
        "exists": state_path.exists(),
        "readable": False,
        "identity_relation": "missing",
        "campaign_identity_hash": None,
    }
    warnings: list[str] = []

    if not state_path.exists():
        warnings.append("state file not found")
        return state_peek, warnings

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive on corrupt input
        state_peek["identity_relation"] = "unreadable"
        warnings.append(f"state file unreadable: {exc}")
        return state_peek, warnings

    state_peek["readable"] = True
    state_peek["campaign_identity_hash"] = payload.get("campaign_identity_hash")
    if campaign_identity_hash and payload.get("campaign_identity_hash") == campaign_identity_hash:
        state_peek["identity_relation"] = "match"
    else:
        state_peek["identity_relation"] = "mismatch"
        warnings.append("state identity mismatch detected")
    return state_peek, warnings


def _load_campaign(campaign_path: Path) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    warnings: list[str] = []
    if not campaign_path.exists():
        return None, ["campaign file not found"], warnings

    try:
        payload = yaml.safe_load(campaign_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"invalid yaml: {exc}"], warnings

    if payload is None:
        return None, ["campaign file is empty"], warnings

    issues = _validate_campaign(payload)
    return payload, issues, warnings


def build_handoff(campaign_path: Path, state_path: Path, output_path: Path) -> dict[str, Any]:
    campaign, validation_issues, warnings = _load_campaign(campaign_path)
    campaign_valid = not validation_issues and campaign is not None

    campaign_identity_hash = _identity_hash(campaign) if campaign_valid else None
    runtime_config_hash = _runtime_hash(campaign) if campaign_valid else None
    state_peek, state_warnings = _peek_state(state_path, campaign_identity_hash=campaign_identity_hash)
    warnings.extend(state_warnings)

    outcome = "ok" if campaign_valid else "blocked_config"
    next_state = "HYDRATE_STATE" if campaign_valid else "BLOCKED_CONFIG"
    next_action = (
        "hydrate or initialize orchestrator state"
        if campaign_valid
        else "repair ml_metaopt_campaign.yaml"
    )
    summary = (
        "campaign validated; hand off to HYDRATE_STATE"
        if campaign_valid
        else "campaign invalid; block configuration until repaired"
    )

    handoff = {
        "schema_version": 1,
        "producer": "metaopt-load-campaign",
        "phase": "LOAD_CAMPAIGN",
        "outcome": outcome,
        "campaign_path": str(campaign_path),
        "campaign_exists": campaign_path.exists(),
        "campaign_valid": campaign_valid,
        "campaign_id": campaign.get("campaign_id") if isinstance(campaign, dict) else None,
        "campaign_identity_hash": campaign_identity_hash,
        "runtime_config_hash": runtime_config_hash,
        "goal": campaign.get("goal") if isinstance(campaign, dict) else None,
        "objective_snapshot": campaign.get("objective") if isinstance(campaign, dict) else None,
        "baseline_snapshot": campaign.get("baseline") if isinstance(campaign, dict) else None,
        "stop_conditions": campaign.get("stop_conditions") if isinstance(campaign, dict) else None,
        "proposal_policy": campaign.get("proposal_policy") if isinstance(campaign, dict) else None,
        "dispatch_policy": campaign.get("dispatch_policy") if isinstance(campaign, dict) else None,
        "datasets": campaign.get("datasets") if isinstance(campaign, dict) else None,
        "sanity": campaign.get("sanity") if isinstance(campaign, dict) else None,
        "artifacts": campaign.get("artifacts") if isinstance(campaign, dict) else None,
        "remote_queue": campaign.get("remote_queue") if isinstance(campaign, dict) else None,
        "execution": campaign.get("execution") if isinstance(campaign, dict) else None,
        "validation_issues": validation_issues,
        "warnings": warnings,
        "state_peek": state_peek,
        "recommended_next_machine_state": next_state,
        "recommended_next_action": next_action,
        "summary": summary,
    }

    return emit_handoff(
        output_path,
        handoff,
        handoff_type="LOAD_CAMPAIGN",
        control_agent="metaopt-load-campaign",
    )


def main() -> int:
    args = _parse_args()
    handoff = build_handoff(
        campaign_path=Path(args.campaign_path),
        state_path=Path(args.state_path),
        output_path=Path(args.output),
    )
    print(json.dumps(handoff, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

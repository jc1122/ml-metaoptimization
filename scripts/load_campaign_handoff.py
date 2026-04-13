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

_HANDOFF_TYPE = "load_campaign.validate"

REQUIRED_TOP_LEVEL_FIELDS = (
    "campaign",
    "project",
    "wandb",
    "compute",
    "objective",
    "proposal_policy",
    "stop_conditions",
)
REQUIRED_NESTED_FIELDS = (
    ("campaign", "name"),
    ("project", "repo"),
    ("project", "smoke_test_command"),
    ("wandb", "entity"),
    ("wandb", "project"),
    ("compute", "provider"),
    ("compute", "accelerator"),
    ("compute", "num_sweep_agents"),
    ("compute", "max_budget_usd"),
    ("objective", "metric"),
    ("objective", "direction"),
    ("objective", "improvement_threshold"),
    ("proposal_policy", "current_target"),
    ("stop_conditions", "max_iterations"),
    ("stop_conditions", "max_no_improve_iterations"),
)
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

    for key in REQUIRED_TOP_LEVEL_FIELDS:
        if key not in campaign:
            issues.append(f"missing required field: {key}")

    for path in REQUIRED_NESTED_FIELDS:
        if _get_nested(campaign, path) is None:
            issues.append(f"missing required field: {'.'.join(path)}")

    direction = _get_nested(campaign, ("objective", "direction"))
    if direction is not None and direction not in ("maximize", "minimize"):
        issues.append("objective.direction must be 'maximize' or 'minimize'")

    for path in (
        ("project", "repo"),
        ("project", "smoke_test_command"),
    ):
        value = _get_nested(campaign, path)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            issues.append(f"{'.'.join(path)} must be a non-empty string")
        elif _contains_sentinel(value):
            issues.append(f"{'.'.join(path)} contains a sentinel placeholder")

    if _contains_sentinel(campaign.get("wandb")):
        issues.append("wandb contains a sentinel placeholder")

    # Numeric range constraints
    num_agents = _get_nested(campaign, ("compute", "num_sweep_agents"))
    if num_agents is not None:
        if isinstance(num_agents, bool):
            issues.append("compute.num_sweep_agents must be an integer, got boolean")
        elif not isinstance(num_agents, int) or not (1 <= num_agents <= 16):
            issues.append("compute.num_sweep_agents must be an integer in [1, 16]")

    max_budget = _get_nested(campaign, ("compute", "max_budget_usd"))
    if max_budget is not None:
        if isinstance(max_budget, bool):
            issues.append("compute.max_budget_usd must be a number, got boolean")
        elif not isinstance(max_budget, (int, float)) or max_budget <= 0 or max_budget > 100:
            issues.append("compute.max_budget_usd must be a number in (0, 100]")

    idle_timeout = _get_nested(campaign, ("compute", "idle_timeout_minutes"))
    if idle_timeout is not None:
        if isinstance(idle_timeout, bool):
            issues.append("compute.idle_timeout_minutes must be an integer, got boolean")
        elif not isinstance(idle_timeout, int) or not (5 <= idle_timeout <= 60):
            issues.append("compute.idle_timeout_minutes must be an integer in [5, 60]")

    improvement_threshold = _get_nested(campaign, ("objective", "improvement_threshold"))
    if improvement_threshold is not None:
        if isinstance(improvement_threshold, bool):
            issues.append("objective.improvement_threshold must be a number, got boolean")
        elif not isinstance(improvement_threshold, (int, float)) or improvement_threshold <= 0:
            issues.append("objective.improvement_threshold must be a positive number")

    max_iterations = _get_nested(campaign, ("stop_conditions", "max_iterations"))
    if max_iterations is not None:
        if isinstance(max_iterations, bool):
            issues.append("stop_conditions.max_iterations must be an integer, got boolean")
        elif not isinstance(max_iterations, int) or max_iterations <= 0:
            issues.append("stop_conditions.max_iterations must be a positive integer")

    max_no_improve = _get_nested(campaign, ("stop_conditions", "max_no_improve_iterations"))
    if max_no_improve is not None:
        if isinstance(max_no_improve, bool):
            issues.append("stop_conditions.max_no_improve_iterations must be an integer, got boolean")
        elif not isinstance(max_no_improve, int) or max_no_improve <= 0:
            issues.append("stop_conditions.max_no_improve_iterations must be a positive integer")

    return issues


def _identity_hash(campaign: dict[str, Any]) -> str:
    payload = {
        "campaign_name": _get_nested(campaign, ("campaign", "name")),
        "objective": {
            "metric": _get_nested(campaign, ("objective", "metric")),
            "direction": _get_nested(campaign, ("objective", "direction")),
        },
        "wandb": {
            "entity": _get_nested(campaign, ("wandb", "entity")),
            "project": _get_nested(campaign, ("wandb", "project")),
        },
    }
    return _sha256(payload)


RECOGNIZED_PREFLIGHT_SCHEMA_VERSIONS = {1}


def _evaluate_preflight(
    state_dir: Path,
    *,
    campaign_identity_hash: str | None,
) -> dict[str, Any]:
    artifact_path = state_dir / "preflight-readiness.json"
    peek: dict[str, Any] = {
        "path": str(artifact_path),
        "exists": False,
        "readable": False,
        "binding_fresh": False,
        "status": "missing",
        "failures": [],
        "artifact_next_action": None,
    }

    if not artifact_path.exists():
        return peek

    peek["exists"] = True

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:
        peek["status"] = "unreadable"
        return peek

    if not isinstance(artifact, dict):
        peek["status"] = "unreadable"
        return peek

    schema_version = artifact.get("schema_version")
    if schema_version not in RECOGNIZED_PREFLIGHT_SCHEMA_VERSIONS:
        peek["readable"] = True
        peek["status"] = "stale"
        return peek

    peek["readable"] = True

    art_identity = artifact.get("campaign_identity_hash")
    binding_fresh = campaign_identity_hash is not None and art_identity == campaign_identity_hash
    peek["binding_fresh"] = binding_fresh

    if not binding_fresh:
        peek["status"] = "stale"
        return peek

    art_status = artifact.get("status")
    art_failures = artifact.get("failures", [])
    art_next_action = artifact.get("next_action")
    peek["artifact_next_action"] = art_next_action
    peek["failures"] = art_failures

    if art_status == "READY":
        peek["status"] = "fresh_ready"
    elif art_status == "FAILED":
        peek["status"] = "fresh_failed"
    else:
        peek["status"] = "stale"
        peek["binding_fresh"] = False

    return peek


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
    except Exception as exc:
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
    state_peek, state_warnings = _peek_state(state_path, campaign_identity_hash=campaign_identity_hash)
    warnings.extend(state_warnings)

    state_dir = state_path.parent

    if campaign_valid:
        preflight = _evaluate_preflight(
            state_dir,
            campaign_identity_hash=campaign_identity_hash,
        )
    else:
        artifact_path = state_dir / "preflight-readiness.json"
        preflight = {
            "path": str(artifact_path),
            "exists": artifact_path.exists(),
            "readable": False,
            "binding_fresh": False,
            "status": "not_evaluated",
            "failures": [],
            "artifact_next_action": None,
        }

    if not campaign_valid:
        next_state = "BLOCKED_CONFIG"
        recovery_action = "repair ml_metaopt_campaign.yaml"
        summary = "campaign invalid; repair ml_metaopt_campaign.yaml before retrying"
    elif preflight["status"] == "fresh_ready":
        next_state = "HYDRATE_STATE"
        recovery_action = None
        summary = "campaign validated; hand off to HYDRATE_STATE"
    elif preflight["status"] == "fresh_failed":
        next_state = "BLOCKED_CONFIG"
        recovery_action = preflight["artifact_next_action"] or "resolve preflight failures and re-run metaopt-preflight"
        summary = f"campaign valid but preflight failed; {recovery_action}"
    elif preflight["status"] == "missing":
        next_state = "BLOCKED_CONFIG"
        recovery_action = "run metaopt-preflight to verify environment readiness"
        summary = "campaign valid but preflight readiness artifact missing; run metaopt-preflight to proceed"
    else:
        next_state = "BLOCKED_CONFIG"
        recovery_action = "re-run metaopt-preflight (campaign configuration has changed or artifact is invalid)"
        summary = "campaign valid but preflight readiness artifact is stale; re-run metaopt-preflight"

    handoff = {
        "schema_version": 1,
        "campaign_path": str(campaign_path),
        "campaign_exists": campaign_path.exists(),
        "campaign_valid": campaign_valid,
        "campaign_id": _get_nested(campaign, ("campaign", "name")) if isinstance(campaign, dict) else None,
        "campaign_identity_hash": campaign_identity_hash,
        "objective_snapshot": campaign.get("objective") if isinstance(campaign, dict) else None,
        "stop_conditions": campaign.get("stop_conditions") if isinstance(campaign, dict) else None,
        "proposal_policy": campaign.get("proposal_policy") if isinstance(campaign, dict) else None,
        "compute": campaign.get("compute") if isinstance(campaign, dict) else None,
        "wandb": campaign.get("wandb") if isinstance(campaign, dict) else None,
        "project": campaign.get("project") if isinstance(campaign, dict) else None,
        "validation_issues": validation_issues,
        "warnings": warnings,
        "state_peek": state_peek,
        "preflight_readiness": preflight,
        "recommended_next_machine_state": next_state,
        "recovery_action": recovery_action,
        "state_patch": None,
        "summary": summary,
    }

    return emit_handoff(
        output_path,
        handoff,
        handoff_type=_HANDOFF_TYPE,
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

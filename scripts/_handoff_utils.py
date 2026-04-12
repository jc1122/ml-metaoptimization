"""Shared utilities for control-protocol handoff construction."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _guardrail_utils import normalize_launch_requests, validate_executor_policy


TERMINAL_MACHINE_STATES = frozenset({"BLOCKED_CONFIG", "BLOCKED_PROTOCOL", "COMPLETE", "FAILED"})
_NO_DIFF = object()
LEGACY_HANDOFF_FIELDS = frozenset(
    {
        "producer",
        "phase",
        "outcome",
        "recommended_next_action",
        "recommended_executor_phase",
        "executor_directives",  # split into pre_launch_directives + post_launch_directives
    }
)

# Allowed patch ownership prefixes per control agent.
STATE_PATCH_OWNERSHIP: dict[str, tuple[tuple[str, ...], ...]] = {
    "metaopt-load-campaign": tuple(),
    "metaopt-hydrate-state": (
        ("version",),
        ("campaign_id",),
        ("campaign_identity_hash",),
        ("runtime_config_hash",),
        ("current_iteration",),
        ("next_action",),
        ("objective_snapshot",),
        ("proposal_cycle",),
        ("active_slots",),
        ("current_proposals",),
        ("next_proposals",),
        ("selected_experiment",),
        ("local_changeset",),
        ("remote_batches",),
        ("baseline",),
        ("completed_experiments",),
        ("key_learnings",),
        ("no_improve_iterations",),
        ("maintenance_summary",),
        ("runtime_capabilities",),
        ("campaign_started_at",),
    ),
    "metaopt-background-control": (
        ("proposal_cycle",),
        ("current_proposals",),
        ("next_proposals",),
        ("next_action",),
        ("maintenance_summary",),
    ),
    "metaopt-select-design": (
        ("selected_experiment",),
        ("proposal_cycle", "current_pool_frozen"),
        ("next_action",),
    ),
    "metaopt-local-execution-control": (
        ("local_changeset",),
        ("selected_experiment", "sanity_attempts"),
        ("selected_experiment", "diagnosis_history"),
        ("next_action",),
    ),
    "metaopt-remote-execution-control": (
        ("pending_remote_batch",),
        ("remote_batches",),
        ("selected_experiment", "analysis_summary"),
        ("selected_experiment", "diagnosis_history"),
        ("baseline",),
        ("no_improve_iterations",),
        ("completed_experiments",),
        ("key_learnings",),
        ("next_action",),
    ),
    "metaopt-iteration-close-control": (
        ("current_iteration",),
        ("current_proposals",),
        ("next_proposals",),
        ("selected_experiment",),
        ("local_changeset",),
        ("completed_experiments",),
        ("key_learnings",),
        ("active_slots",),
        ("last_iteration_report",),
        ("next_action",),
    ),
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def derive_status_for_machine_state(machine_state: str | None) -> str | None:
    if machine_state is None:
        return None
    if machine_state in TERMINAL_MACHINE_STATES:
        return machine_state
    return "RUNNING"


def _diff_values(before: Any, after: Any) -> Any:
    if before == after:
        return _NO_DIFF
    if isinstance(before, dict) and isinstance(after, dict):
        patch: dict[str, Any] = {}
        for key in after:
            child = _diff_values(before.get(key), after[key])
            if child is not _NO_DIFF:
                patch[key] = child
        return patch if patch else _NO_DIFF
    return deepcopy(after)


def _leaf_paths(payload: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if isinstance(payload, dict):
        if not payload:
            return [prefix]
        paths: list[tuple[str, ...]] = []
        for key, value in payload.items():
            paths.extend(_leaf_paths(value, prefix + (str(key),)))
        return paths
    return [prefix]


def _is_allowed_patch_path(control_agent: str, path: tuple[str, ...]) -> bool:
    allowed_prefixes = STATE_PATCH_OWNERSHIP.get(control_agent)
    if allowed_prefixes is None:
        raise ValueError(f"unknown control agent for state patch ownership: {control_agent!r}")
    return any(path[: len(prefix)] == prefix for prefix in allowed_prefixes)


def validate_state_patch(control_agent: str, state_patch: dict[str, Any] | None) -> dict[str, Any] | None:
    if state_patch is None:
        return None
    if not isinstance(state_patch, dict):
        raise TypeError(f"state_patch must be a dict or null, got {type(state_patch).__name__}")
    if "machine_state" in state_patch:
        raise ValueError("state_patch must not contain machine_state")
    if "status" in state_patch:
        raise ValueError("state_patch must not contain status")

    for path in _leaf_paths(state_patch):
        if path and not _is_allowed_patch_path(control_agent, path):
            raise ValueError(
                f"state_patch path {'.'.join(path)!r} is not owned by {control_agent!r}"
            )
    return state_patch


def compute_state_patch(
    previous_state: dict[str, Any],
    next_state: dict[str, Any],
    control_agent: str,
) -> dict[str, Any]:
    if not isinstance(previous_state, dict):
        raise TypeError("previous_state must be a dict")
    if not isinstance(next_state, dict):
        raise TypeError("next_state must be a dict")

    before = deepcopy(previous_state)
    after = deepcopy(next_state)
    before.pop("machine_state", None)
    before.pop("status", None)
    after.pop("machine_state", None)
    after.pop("status", None)

    patch = _diff_values(before, after)
    if patch is _NO_DIFF:
        return {}
    if not isinstance(patch, dict):
        raise TypeError("top-level state patch must be a dict")
    return validate_state_patch(control_agent, patch)


def load_campaign_handoff_is_ready(load_handoff: Any) -> bool:
    if not isinstance(load_handoff, dict):
        return False
    return (
        load_handoff.get("control_agent") == "metaopt-load-campaign"
        and load_handoff.get("recommended_next_machine_state") == "HYDRATE_STATE"
        and load_handoff.get("campaign_valid") is True
    )


def _merge_state_patch(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = deepcopy(base)
        for key, value in patch.items():
            if key in merged:
                merged[key] = _merge_state_patch(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(patch)


def apply_state_patch(
    previous_state: dict[str, Any],
    state_patch: dict[str, Any] | None,
    recommended_next_machine_state: str | None,
) -> dict[str, Any]:
    if not isinstance(previous_state, dict):
        raise TypeError("previous_state must be a dict")

    patch = state_patch or {}
    merged = _merge_state_patch(previous_state, patch)

    if recommended_next_machine_state is not None:
        merged["machine_state"] = recommended_next_machine_state
    machine_state = merged.get("machine_state")
    derived_status = derive_status_for_machine_state(machine_state)
    if derived_status is not None:
        merged["status"] = derived_status
    return merged


def persist_state_handoff(
    state_path: Path,
    previous_state: dict[str, Any],
    next_state: dict[str, Any],
    payload: dict[str, Any],
    *,
    control_agent: str,
) -> dict[str, Any]:
    payload["state_patch"] = compute_state_patch(previous_state, next_state, control_agent)
    persisted_state = apply_state_patch(
        previous_state,
        payload["state_patch"],
        payload.get("recommended_next_machine_state"),
    )
    write_json(state_path, persisted_state)
    return persisted_state


def normalize_directives(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate and normalize executor directives into list[dict].

    Each directive must contain at least ``action`` (non-empty str) and
    ``reason`` (non-empty str).  Extra keys are preserved.

    Returns an empty list when *raw* is ``None``.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"directive list must be a list, got {type(raw).__name__}")
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(f"directives[{i}] must be a dict, got {type(entry).__name__}")
        if "action" not in entry or not isinstance(entry["action"], str) or not entry["action"]:
            raise ValueError(f"directives[{i}] must have a non-empty 'action' string")
        if "reason" not in entry or not isinstance(entry["reason"], str) or not entry["reason"]:
            raise ValueError(f"directives[{i}] must have a non-empty 'reason' string")
    return raw


def emit_handoff(
    output_path: Path,
    payload: dict[str, Any],
    *,
    handoff_type: str,
    control_agent: str,
) -> dict[str, Any]:
    """Write handoff JSON, injecting universal control-protocol envelope keys.

    Keys already present in *payload* are preserved; only missing envelope
    keys receive their defaults (empty list / empty dict).
    """
    legacy_keys = sorted(LEGACY_HANDOFF_FIELDS & payload.keys())
    if legacy_keys:
        raise ValueError(
            f"legacy handoff fields are no longer allowed: {legacy_keys}"
        )

    payload.setdefault("handoff_type", handoff_type)
    payload.setdefault("control_agent", control_agent)
    payload.setdefault("launch_requests", [])
    payload["launch_requests"] = normalize_launch_requests(payload["launch_requests"])
    if "state_patch" not in payload:
        raise ValueError("state_patch must be provided explicitly as a dict or null")
    payload["state_patch"] = validate_state_patch(control_agent, payload["state_patch"])
    payload.setdefault("pre_launch_directives", [])
    payload["pre_launch_directives"] = normalize_directives(payload["pre_launch_directives"])
    payload["pre_launch_directives"] = validate_executor_policy(
        control_agent, payload["handoff_type"], payload["pre_launch_directives"]
    )
    payload.setdefault("post_launch_directives", [])
    payload["post_launch_directives"] = normalize_directives(payload["post_launch_directives"])
    payload["post_launch_directives"] = validate_executor_policy(
        control_agent, payload["handoff_type"], payload["post_launch_directives"]
    )
    payload.setdefault("summary", "")
    payload.setdefault("warnings", [])
    write_json(output_path, payload)
    return payload

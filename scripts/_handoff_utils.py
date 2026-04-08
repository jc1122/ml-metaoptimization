"""Shared utilities for control-protocol handoff construction."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _guardrail_utils import normalize_launch_requests, validate_executor_policy


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_directives(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate and normalize executor directives into list[dict].

    Each directive must contain at least ``action`` (non-empty str) and
    ``reason`` (non-empty str).  Extra keys are preserved.

    Returns an empty list when *raw* is ``None``.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"executor_directives must be a list, got {type(raw).__name__}")
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(f"executor_directives[{i}] must be a dict, got {type(entry).__name__}")
        if "action" not in entry or not isinstance(entry["action"], str) or not entry["action"]:
            raise ValueError(f"executor_directives[{i}] must have a non-empty 'action' string")
        if "reason" not in entry or not isinstance(entry["reason"], str) or not entry["reason"]:
            raise ValueError(f"executor_directives[{i}] must have a non-empty 'reason' string")
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
    payload.setdefault("handoff_type", handoff_type)
    payload.setdefault("control_agent", control_agent)
    payload.setdefault("launch_requests", [])
    payload["launch_requests"] = normalize_launch_requests(payload["launch_requests"])
    payload.setdefault("state_patch", {})
    payload.setdefault("executor_directives", [])
    payload["executor_directives"] = normalize_directives(payload["executor_directives"])
    payload["executor_directives"] = validate_executor_policy(
        control_agent, payload.get("phase"), payload["executor_directives"]
    )
    payload.setdefault("summary", "")
    payload.setdefault("warnings", [])
    write_json(output_path, payload)
    return payload

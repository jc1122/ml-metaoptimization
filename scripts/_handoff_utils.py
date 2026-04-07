"""Shared utilities for control-protocol handoff construction."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    payload.setdefault("state_patch", {})
    payload.setdefault("executor_directives", [])
    payload.setdefault("summary", "")
    payload.setdefault("warnings", [])
    write_json(output_path, payload)
    return payload

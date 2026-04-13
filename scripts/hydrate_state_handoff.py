from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _handoff_utils import (
    emit_handoff,
    load_campaign_handoff_is_ready,
    persist_state_handoff,
    read_json,
    timestamp,
    write_json,
)


AGENTS_HOOK = """<!-- ml-metaoptimization:begin -->
## Active task
`.ml-metaopt/state.json` exists. Inspect its `status`.
If `status` is `RUNNING`, invoke the `ml-metaoptimization` skill immediately,
resume from `machine_state`, and do not ask the user for campaign inputs.
If `status` is terminal, invoke the `ml-metaoptimization` skill once so terminal cleanup can run through control-agent directives; do not execute `next_action`.
<!-- ml-metaoptimization:end -->
"""
AGENTS_HOOK_RE = re.compile(
    r"<!-- ml-metaoptimization:begin -->.*?<!-- ml-metaoptimization:end -->\n?",
    re.DOTALL,
)
STATE_REQUIRED_KEYS = {
    "version",
    "campaign_id",
    "campaign_identity_hash",
    "status",
    "machine_state",
    "current_iteration",
    "next_action",
    "objective_snapshot",
    "proposal_cycle",
    "current_proposals",
    "next_proposals",
    "selected_sweep",
    "current_sweep",
    "baseline",
    "completed_iterations",
    "key_learnings",
    "no_improve_iterations",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an authoritative HYDRATE_STATE handoff for the orchestrator.")
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--agents-path", required=True)
    parser.add_argument("--skills-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--apply-state",
        action="store_true",
        default=False,
        help="Test/orchestrator harness mode: apply the computed state_patch to state-path.",
    )
    return parser.parse_args()


_HANDOFF_TYPE = "hydrate_state.hydrate"
_CONTROL_AGENT = "metaopt-hydrate-state"
_TERMINAL_STATUSES = {"COMPLETE", "BLOCKED_CONFIG", "BLOCKED_PROTOCOL", "FAILED"}


def _runtime_error(
    output_path: Path,
    recovery_action: str,
    summary: str,
    *,
    warnings: list[str] | None = None,
    state_preserved: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "state_path": None,
        "state_written": False,
        "state_preserved": state_preserved,
        "campaign_id": None,
        "campaign_identity_hash": None,
        "resume_mode": "none",
        "effective_status": None,
        "effective_machine_state": None,
        "recommended_next_machine_state": None,
        "recovery_action": recovery_action,
        "agents_hook_action": "unchanged",
        "state_patch": None,
        "warnings": warnings or [],
        "summary": summary,
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _load_step1_handoff(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None
    return payload


def _probe_skills(manifest_path: Path) -> dict[str, Any]:
    payload = read_json(manifest_path)
    skills = payload.get("skills")
    if not isinstance(skills, list):
        raise ValueError("skills manifest must contain a list under 'skills'")

    available: list[str] = []
    missing: list[str] = []
    degraded_lanes: list[str] = []
    blocking_skill: str | None = None

    for skill in skills:
        name = skill["name"]
        classification = skill["classification"]
        lane = skill["lane"]
        probe_paths = [Path(p) for p in skill.get("probe_paths", [])]
        found = any(path.exists() for path in probe_paths)
        if found:
            available.append(name)
            continue
        missing.append(name)
        if classification == "required" and blocking_skill is None:
            blocking_skill = name
        if classification == "degradable":
            degraded_lanes.append(skill.get("degraded_lane", lane))

    return {
        "verified_at": timestamp(),
        "available_skills": sorted(available),
        "missing_skills": sorted(missing),
        "degraded_lanes": sorted(set(degraded_lanes)),
        "blocking_skill": blocking_skill,
    }


def _ensure_hook(path: Path) -> str:
    if not path.exists():
        path.write_text(AGENTS_HOOK, encoding="utf-8")
        return "created"
    content = path.read_text(encoding="utf-8")
    stripped = AGENTS_HOOK_RE.sub("", content).rstrip()
    if AGENTS_HOOK in content:
        return "unchanged"
    new_content = f"{stripped}\n\n{AGENTS_HOOK}" if stripped else AGENTS_HOOK
    path.write_text(new_content, encoding="utf-8")
    return "updated"


def _remove_hook_directive() -> dict[str, str]:
    return {
        "action": "remove_agents_hook",
        "reason": "terminal or blocked hydrate outcome; orchestration hook cleanup is orchestrator-owned",
        "agents_path": "AGENTS.md",
    }


def _validate_existing_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("existing state must be a JSON object")
    missing = sorted(STATE_REQUIRED_KEYS - state.keys())
    if missing:
        raise ValueError(f"existing state missing keys: {missing}")
    return state


def _fresh_state(load_handoff: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 4,
        "campaign_id": load_handoff["campaign_id"],
        "campaign_identity_hash": load_handoff["campaign_identity_hash"],
        "status": "RUNNING",
        "machine_state": "IDEATE",
        "current_iteration": 1,
        "next_action": "maintain background pool",
        "objective_snapshot": load_handoff["objective_snapshot"],
        "proposal_cycle": {
            "cycle_id": "iter-1-cycle-1",
            "current_pool_frozen": False,
        },
        "current_proposals": [],
        "next_proposals": [],
        "selected_sweep": None,
        "current_sweep": None,
        "baseline": None,
        "completed_iterations": [],
        "key_learnings": [],
        "no_improve_iterations": 0,
        "campaign_started_at": timestamp(),
    }


def build_handoff(
    load_handoff_path: Path,
    state_path: Path,
    agents_path: Path,
    skills_manifest_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    try:
        load_handoff = _load_step1_handoff(load_handoff_path)
    except Exception as exc:
        return _runtime_error(
            output_path,
            "repair or replace load_campaign.latest.json",
            "load handoff unreadable",
            warnings=[str(exc)],
        )

    if not load_campaign_handoff_is_ready(load_handoff):
        return _runtime_error(
            output_path,
            "repair or regenerate load_campaign.latest.json",
            "load handoff invalid",
        )

    try:
        runtime_capabilities = _probe_skills(skills_manifest_path)
    except Exception as exc:
        return _runtime_error(
            output_path,
            "repair or replace agents/worker-skills.json",
            "skills manifest unreadable",
            warnings=[str(exc)],
        )

    warnings: list[str] = []
    state_written = False
    state_preserved = False
    resume_mode = "none"
    previous_state: dict[str, Any] = {}

    if state_path.exists():
        try:
            existing_state = _validate_existing_state(read_json(state_path))
        except Exception as exc:
            warnings.append(f"existing state unreadable: {exc}")
            return _runtime_error(
                output_path,
                "repair or replace .ml-metaopt/state.json",
                "existing state unreadable",
                warnings=warnings,
                state_preserved=True,
            )

        if existing_state["campaign_identity_hash"] != load_handoff["campaign_identity_hash"]:
            payload = {
                "schema_version": 1,
                "state_path": str(state_path),
                "state_written": False,
                "state_preserved": True,
                "campaign_id": load_handoff["campaign_id"],
                "campaign_identity_hash": load_handoff["campaign_identity_hash"],
                "resume_mode": "none",
                "effective_status": "BLOCKED_CONFIG",
                "effective_machine_state": "BLOCKED_CONFIG",
                "recommended_next_machine_state": "BLOCKED_CONFIG",
                "recovery_action": "archive or remove the stale state before starting a new campaign",
                "agents_hook_action": "remove_directive_emitted",
                "state_patch": None,
                "directives": [_remove_hook_directive()],
                "warnings": warnings,
                "summary": "state identity mismatch detected; preserved stale state and blocked resume",
            }
            return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

        state = existing_state
        previous_state = deepcopy(existing_state)
        state.setdefault("campaign_started_at", timestamp())
        resume_mode = "existing"
        if state["status"] in _TERMINAL_STATUSES:
            outcome = "terminal"
        else:
            outcome = "resumed"
    else:
        state = _fresh_state(load_handoff)
        resume_mode = "fresh"
        outcome = "initialized"
        previous_state = {}

    if runtime_capabilities["blocking_skill"] and outcome != "terminal":
        state["status"] = "BLOCKED_CONFIG"
        state["machine_state"] = "BLOCKED_CONFIG"
        state["next_action"] = f"install missing skill: {runtime_capabilities['blocking_skill']}"
        outcome = "blocked_config"

    if state["status"] == "RUNNING":
        agents_action = _ensure_hook(agents_path)
        directives: list[dict[str, str]] = []
    else:
        agents_action = "remove_directive_emitted"
        directives = [_remove_hook_directive()]

    payload = {
        "schema_version": 1,
        "state_path": str(state_path),
        "state_written": state_written,
        "state_preserved": state_preserved,
        "campaign_id": state["campaign_id"],
        "campaign_identity_hash": state["campaign_identity_hash"],
        "resume_mode": resume_mode,
        "effective_status": state["status"],
        "effective_machine_state": state["machine_state"],
        "recommended_next_machine_state": state["machine_state"],
        "recovery_action": None,
        "agents_hook_action": agents_action,
        "directives": directives,
        "warnings": warnings,
        "summary": (
            "fresh orchestrator state initialized"
            if outcome == "initialized"
            else "existing orchestrator state resumed"
            if outcome == "resumed"
            else f"existing state is terminal ({state['status']}); hook removal directive emitted"
            if outcome == "terminal"
            else "required skill missing; blocked-state handoff emitted"
        ),
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    payload["state_written"] = payload["state_applied"]
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    if args.apply_state:
        os.environ["METAOPT_APPLY_STATE_HANDOFF"] = "1"
    payload = build_handoff(
        load_handoff_path=Path(args.load_handoff),
        state_path=Path(args.state_path),
        agents_path=Path(args.agents_path),
        skills_manifest_path=Path(args.skills_manifest),
        output_path=Path(args.output),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENTS_HOOK = """<!-- ml-metaoptimization:begin -->
## Active task
`.ml-metaopt/state.json` exists. Inspect its `status`.
If `status` is `RUNNING`, invoke the `ml-metaoptimization` skill immediately,
resume from `machine_state`, and do not ask the user for campaign inputs.
If `status` is terminal, remove this block and follow `next_action` instead of auto-resuming.
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
    "runtime_config_hash",
    "status",
    "machine_state",
    "current_iteration",
    "next_action",
    "objective_snapshot",
    "proposal_cycle",
    "active_slots",
    "current_proposals",
    "next_proposals",
    "selected_experiment",
    "local_changeset",
    "remote_batches",
    "baseline",
    "completed_experiments",
    "key_learnings",
    "no_improve_iterations",
    "runtime_capabilities",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an authoritative HYDRATE_STATE handoff for the orchestrator.")
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--agents-path", required=True)
    parser.add_argument("--skills-manifest", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _runtime_error(output_path: Path, summary: str, *, warnings: list[str] | None = None, state_preserved: bool = False) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "producer": "metaopt-hydrate-state",
        "phase": "HYDRATE_STATE",
        "outcome": "runtime_error",
        "state_path": None,
        "state_written": False,
        "state_preserved": state_preserved,
        "campaign_id": None,
        "campaign_identity_hash": None,
        "runtime_config_hash": None,
        "resume_mode": "none",
        "effective_status": None,
        "effective_machine_state": None,
        "recommended_next_machine_state": None,
        "recommended_next_action": summary,
        "runtime_capabilities": None,
        "agents_hook_action": "unchanged",
        "warnings": warnings or [],
        "summary": summary,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _load_step1_handoff(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return None
    return payload


def _probe_skills(manifest_path: Path) -> dict[str, Any]:
    payload = _read_json(manifest_path)
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
        "verified_at": _timestamp(),
        "available_skills": sorted(available),
        "missing_skills": sorted(missing),
        "degraded_lanes": sorted(set(degraded_lanes)),
        "blocking_skill": blocking_skill,
    }


def _strip_hook(content: str) -> str:
    updated = AGENTS_HOOK_RE.sub("", content)
    return updated.strip() + ("\n" if updated.strip() else "")


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


def _remove_hook(path: Path) -> str:
    if not path.exists():
        return "unchanged"
    content = path.read_text(encoding="utf-8")
    updated = _strip_hook(content)
    if updated == content:
        return "unchanged"
    path.write_text(updated, encoding="utf-8")
    return "removed"


def _validate_existing_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("existing state must be a JSON object")
    missing = sorted(STATE_REQUIRED_KEYS - state.keys())
    if missing:
        raise ValueError(f"existing state missing keys: {missing}")
    return state


def _fresh_state(load_handoff: dict[str, Any], runtime_capabilities: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 3,
        "campaign_id": load_handoff["campaign_id"],
        "campaign_identity_hash": load_handoff["campaign_identity_hash"],
        "runtime_config_hash": load_handoff["runtime_config_hash"],
        "status": "RUNNING",
        "machine_state": "MAINTAIN_BACKGROUND_POOL",
        "current_iteration": 1,
        "next_action": "maintain background slot pool",
        "objective_snapshot": load_handoff["objective_snapshot"],
        "proposal_cycle": {
            "cycle_id": "iter-1-cycle-1",
            "current_pool_frozen": False,
            "ideation_rounds_by_slot": {},
            "shortfall_reason": "",
        },
        "active_slots": [],
        "current_proposals": [],
        "next_proposals": [],
        "selected_experiment": None,
        "local_changeset": None,
        "remote_batches": [],
        "baseline": load_handoff["baseline_snapshot"],
        "completed_experiments": [],
        "key_learnings": [],
        "no_improve_iterations": 0,
        "runtime_capabilities": {
            "verified_at": runtime_capabilities["verified_at"],
            "available_skills": runtime_capabilities["available_skills"],
            "missing_skills": runtime_capabilities["missing_skills"],
            "degraded_lanes": runtime_capabilities["degraded_lanes"],
        },
    }


def _blocked_state(load_handoff: dict[str, Any], runtime_capabilities: dict[str, Any], next_action: str) -> dict[str, Any]:
    state = _fresh_state(load_handoff, runtime_capabilities)
    state["status"] = "BLOCKED_CONFIG"
    state["machine_state"] = "BLOCKED_CONFIG"
    state["next_action"] = next_action
    state["active_slots"] = []
    return state


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
        return _runtime_error(output_path, "repair or replace load_campaign.latest.json", warnings=[str(exc)])

    if not load_handoff or load_handoff.get("outcome") != "ok":
        return _runtime_error(output_path, "repair or regenerate load_campaign.latest.json")

    try:
        runtime_capabilities = _probe_skills(skills_manifest_path)
    except Exception as exc:
        return _runtime_error(output_path, "repair or replace agents/worker-skills.json", warnings=[str(exc)])

    warnings: list[str] = []
    state_written = False
    state_preserved = False
    resume_mode = "none"

    if state_path.exists():
        try:
            existing_state = _validate_existing_state(_read_json(state_path))
        except Exception as exc:
            warnings.append(f"existing state unreadable: {exc}")
            return _runtime_error(
                output_path,
                "repair or replace .ml-metaopt/state.json",
                warnings=warnings,
                state_preserved=True,
            )

        if existing_state["campaign_identity_hash"] != load_handoff["campaign_identity_hash"]:
            agents_action = _remove_hook(agents_path)
            payload = {
                "schema_version": 1,
                "producer": "metaopt-hydrate-state",
                "phase": "HYDRATE_STATE",
                "outcome": "blocked_config",
                "state_path": str(state_path),
                "state_written": False,
                "state_preserved": True,
                "campaign_id": load_handoff["campaign_id"],
                "campaign_identity_hash": load_handoff["campaign_identity_hash"],
                "runtime_config_hash": load_handoff["runtime_config_hash"],
                "resume_mode": "none",
                "effective_status": "BLOCKED_CONFIG",
                "effective_machine_state": "BLOCKED_CONFIG",
                "recommended_next_machine_state": "BLOCKED_CONFIG",
                "recommended_next_action": "archive or remove the stale state before starting a new campaign",
                "runtime_capabilities": {
                    "verified_at": runtime_capabilities["verified_at"],
                    "available_skills": runtime_capabilities["available_skills"],
                    "missing_skills": runtime_capabilities["missing_skills"],
                    "degraded_lanes": runtime_capabilities["degraded_lanes"],
                },
                "agents_hook_action": agents_action,
                "warnings": warnings,
                "summary": "state identity mismatch detected; preserved stale state and blocked resume",
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return payload

        state = existing_state
        state["runtime_capabilities"] = {
            "verified_at": runtime_capabilities["verified_at"],
            "available_skills": runtime_capabilities["available_skills"],
            "missing_skills": runtime_capabilities["missing_skills"],
            "degraded_lanes": runtime_capabilities["degraded_lanes"],
        }
        resume_mode = "existing"
        outcome = "resumed"
    else:
        state = _fresh_state(load_handoff, runtime_capabilities)
        resume_mode = "fresh"
        outcome = "initialized"

    if runtime_capabilities["blocking_skill"]:
        state = _blocked_state(
            load_handoff,
            runtime_capabilities,
            f"install missing skill: {runtime_capabilities['blocking_skill']}",
        )
        outcome = "blocked_config"

    agents_action = _ensure_hook(agents_path) if state["status"] == "RUNNING" else _remove_hook(agents_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_written = True

    payload = {
        "schema_version": 1,
        "producer": "metaopt-hydrate-state",
        "phase": "HYDRATE_STATE",
        "outcome": outcome,
        "state_path": str(state_path),
        "state_written": state_written,
        "state_preserved": state_preserved,
        "campaign_id": state["campaign_id"],
        "campaign_identity_hash": state["campaign_identity_hash"],
        "runtime_config_hash": state["runtime_config_hash"],
        "resume_mode": resume_mode,
        "effective_status": state["status"],
        "effective_machine_state": state["machine_state"],
        "recommended_next_machine_state": state["machine_state"],
        "recommended_next_action": state["next_action"],
        "runtime_capabilities": state["runtime_capabilities"],
        "agents_hook_action": agents_action,
        "warnings": warnings,
        "summary": (
            "fresh orchestrator state initialized"
            if outcome == "initialized"
            else "existing orchestrator state resumed"
            if outcome == "resumed"
            else "required skill missing; wrote blocked state"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    args = _parse_args()
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

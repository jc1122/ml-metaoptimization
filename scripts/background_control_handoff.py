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
from _handoff_utils import emit_handoff, persist_state_handoff, read_json, timestamp

_CONTROL_AGENT = "metaopt-background-control"
_PLAN_HANDOFF_TYPE = "background_control.plan_background_work"
_GATE_HANDOFF_TYPE = "background_control.gate_background_work"


def _runtime_error(
    output_path: Path,
    handoff_type: str,
    recovery_action: str,
    summary: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "recommended_next_machine_state": None,
        "recovery_action": recovery_action,
        "state_patch": None,
        "warnings": warnings or [],
        "summary": summary,
    }
    return emit_handoff(output_path, payload, handoff_type=handoff_type, control_agent=_CONTROL_AGENT)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Steps 3/4 background control handoffs.")
    parser.add_argument("--mode", required=True, choices=("plan_background_work", "gate_background_work"))
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--worker-results-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--secondary", action="store_true", default=False)
    parser.add_argument(
        "--apply-state",
        action="store_true",
        default=False,
        help="Test/orchestrator harness mode: apply the computed state_patch to state-path.",
    )
    return parser.parse_args()


def _proposal_sequence(state: dict[str, Any]) -> int:
    pattern = re.compile(r".+-p(\d+)$")
    max_seen = 0
    for pool_name in ("current_proposals", "next_proposals"):
        for proposal in state.get(pool_name, []):
            proposal_id = proposal.get("proposal_id")
            if isinstance(proposal_id, str):
                match = pattern.fullmatch(proposal_id)
                if match:
                    max_seen = max(max_seen, int(match.group(1)))
    return max_seen


def _ready_for_selection(state: dict[str, Any], load_handoff: dict[str, Any]) -> bool:
    proposal_policy = load_handoff["proposal_policy"]
    current_count = len(state["current_proposals"])
    return current_count >= proposal_policy["current_target"]


def _task_markdown(slot_id: str, request: dict[str, Any], load_handoff: dict[str, Any], state: dict[str, Any]) -> str:
    lines = [
        f"# Slot Task: {slot_id}",
        "",
        f"- Slot ID: `{slot_id}`",
        f"- Attempt: `1`",
        f"- Mode: `ideation`",
        f"- Worker Kind: `custom_agent`",
        f"- Worker Ref: `{request['worker_ref']}`",
        f"- Model Class: `{request['model_class']}`",
        f"- Result File: `{request['result_file']}`",
        "",
        "Execute only this assigned scope. Do not make control-plane decisions.",
        "Write one structured JSON result file to the exact result path shown above.",
        "",
        "Summary: Generate distinct experiment proposals",
    ]
    objective = load_handoff.get("objective_snapshot", {})
    baseline = state.get("baseline") or {}
    lines.extend([
        "",
        "## Campaign Context",
        f"- Metric: `{objective.get('metric', '')}`",
        f"- Direction: `{objective.get('direction', '')}`",
        f"- Improvement Threshold: `{objective.get('improvement_threshold')}`",
        f"- Baseline: `{json.dumps(baseline, sort_keys=True)}`",
        f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`",
        f"- Current Proposal Pool: `{json.dumps(state.get('current_proposals', []), sort_keys=True)}`",
        f"- Next Proposal Pool Context: `{json.dumps(state.get('next_proposals', []), sort_keys=True)}`",
        f"- Proposal Policy: `{json.dumps(load_handoff.get('proposal_policy', {}), sort_keys=True)}`",
        "",
        "## Output Schema",
        "- `slot_id`",
        "- `mode = \"ideation\"`",
        "- `status`",
        "- `summary`",
        "- `proposal_candidates`",
        "- optional `saturated` and `reason`",
    ])
    return "\n".join(lines) + "\n"


def _plan_background_work(
    load_handoff: dict[str, Any],
    state_path: Path,
    tasks_dir: Path,
    output_path: Path,
    secondary: bool = False,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    proposal_policy = load_handoff["proposal_policy"]

    if _ready_for_selection(state, load_handoff):
        state["next_action"] = "select experiment"
        payload = {
            "schema_version": 1,
            "pool_status": "ready",
            "recommended_next_machine_state": "SELECT_AND_DESIGN_SWEEP",
            "launch_requests": [],
            "summary": "proposal pool already satisfies selection gate",
        }
        if secondary:
            payload["recommended_next_machine_state"] = None
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    needed = max(1, proposal_policy["current_target"] - len(state["current_proposals"]))
    launch_requests: list[dict[str, Any]] = []

    for i in range(needed):
        slot_id = f"bg-{i + 1}"
        result_file = str(Path(".ml-metaopt") / "worker-results" / f"{slot_id}.json")
        task_file_rel = str(Path(".ml-metaopt") / "tasks" / f"{slot_id}.md")
        request = {
            "slot_class": "background",
            "mode": "ideation",
            "worker_ref": "metaopt-ideation-worker",
            "model_class": "general_worker",
            "task_file": task_file_rel,
            "result_file": result_file,
        }
        launch_requests.append(request)
        task_path = tasks_dir / f"{slot_id}.md"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(_task_markdown(slot_id, request, load_handoff, state), encoding="utf-8")

    state["proposal_cycle"]["current_pool_frozen"] = False
    if state["current_iteration"] == 1 and not state["proposal_cycle"]["cycle_id"]:
        state["proposal_cycle"]["cycle_id"] = "iter-1-cycle-1"
    state["next_action"] = "execute planned background work"

    payload = {
        "schema_version": 1,
        "pool_status": "building",
        "recommended_next_machine_state": "WAIT_FOR_PROPOSALS",
        "launch_requests": launch_requests,
        "summary": "background slots planned for continued proposal accumulation",
    }
    if secondary:
        payload["recommended_next_machine_state"] = None
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _gate_background_work(
    load_handoff: dict[str, Any],
    state_path: Path,
    worker_results_dir: Path,
    output_path: Path,
    secondary: bool = False,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    proposal_policy = load_handoff["proposal_policy"]
    sequence = _proposal_sequence(state)
    processed_results: list[str] = []

    # Build set of source_file values already in proposals for dedup by file name
    existing_source_files: set[str] = set()
    for pool_name in ("current_proposals", "next_proposals"):
        for proposal in state.get(pool_name, []):
            sf = proposal.get("source_file")
            if isinstance(sf, str):
                existing_source_files.add(sf)

    for result_file in sorted(worker_results_dir.glob("bg-*.json")):
        result = read_json(result_file)
        file_basename = result_file.name
        processed_results.append(result_file.stem)

        # Skip files already processed (dedup by result file name)
        if file_basename in existing_source_files:
            continue

        if result.get("status") == "completed":
            candidates = result.get("proposal_candidates", [])
            for candidate in candidates:
                sequence += 1
                proposal_id = f"{state['campaign_id']}-p{sequence}"
                enriched = dict(candidate)
                enriched["proposal_id"] = proposal_id
                enriched["source_slot_id"] = result_file.stem
                enriched["source_file"] = file_basename
                enriched["creation_iteration"] = state["current_iteration"]
                enriched["created_at"] = timestamp()
                destination = "current_proposals" if not state["proposal_cycle"]["current_pool_frozen"] else "next_proposals"
                state[destination].append(enriched)
            existing_source_files.add(file_basename)

    if _ready_for_selection(state, load_handoff):
        state["next_action"] = "select experiment"
        recommended_next_machine_state = "SELECT_AND_DESIGN_SWEEP"
        pool_status = "ready"
        summary = "proposal pool satisfies selection gate"
    else:
        state["next_action"] = "plan more background work"
        recommended_next_machine_state = "IDEATE"
        pool_status = "building"
        summary = "proposal pool still below threshold"

    payload = {
        "schema_version": 1,
        "pool_status": pool_status,
        "recommended_next_machine_state": recommended_next_machine_state,
        "current_proposal_count": len(state["current_proposals"]),
        "next_proposal_count": len(state["next_proposals"]),
        "processed_results": processed_results,
        "summary": summary,
    }
    if secondary:
        payload["recommended_next_machine_state"] = None
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_GATE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    if args.apply_state:
        os.environ["METAOPT_APPLY_STATE_HANDOFF"] = "1"
    output_path = Path(args.output)
    handoff_type = _PLAN_HANDOFF_TYPE if args.mode == "plan_background_work" else _GATE_HANDOFF_TYPE

    try:
        load_handoff = read_json(Path(args.load_handoff))
        if not isinstance(load_handoff, dict) or "proposal_policy" not in load_handoff:
            raise ValueError("load handoff missing required field: proposal_policy")
    except Exception as exc:
        payload = _runtime_error(
            output_path,
            handoff_type,
            "repair or replace load_campaign.latest.json",
            "load handoff unreadable or invalid",
            [str(exc)],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    state_path = Path(args.state_path)
    tasks_dir = Path(args.tasks_dir)
    worker_results_dir = Path(args.worker_results_dir)

    if args.mode == "plan_background_work":
        payload = _plan_background_work(load_handoff, state_path, tasks_dir, output_path, secondary=args.secondary)
    else:
        payload = _gate_background_work(load_handoff, state_path, worker_results_dir, output_path, secondary=args.secondary)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

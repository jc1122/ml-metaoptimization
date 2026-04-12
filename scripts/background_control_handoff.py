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
from _guardrail_utils import check_lane_drift
from _handoff_utils import emit_handoff, persist_state_handoff, read_json, timestamp

_CONTROL_AGENT = "metaopt-background-control"
_PLAN_HANDOFF_TYPE = "background_control.plan_background_work"
_GATE_HANDOFF_TYPE = "background_control.gate_background_work"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Steps 3/4 background control handoffs.")
    parser.add_argument("--mode", required=True, choices=("plan_background_work", "gate_background_work"))
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--worker-results-dir", required=True)
    parser.add_argument("--slot-events-dir", required=True)
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
    if current_count >= proposal_policy["current_target"]:
        return True
    rounds = state["proposal_cycle"]["ideation_rounds_by_slot"]
    if rounds and all(count >= 2 for count in rounds.values()) and current_count >= proposal_policy["current_floor"]:
        return True
    return False


def _task_markdown(slot_id: str, request: dict[str, Any], load_handoff: dict[str, Any], state: dict[str, Any]) -> str:
    worker_kind = "skill" if request["mode"] == "maintenance" else "custom_agent"
    task_summary = "Generate distinct experiment proposals" if request["mode"] == "ideation" else "Run findings-only maintenance work"
    lines = [
        f"# Slot Task: {slot_id}",
        "",
        f"- Slot ID: `{slot_id}`",
        f"- Attempt: `1`",
        f"- Mode: `{request['mode']}`",
        f"- Worker Kind: `{worker_kind}`",
        f"- Worker Ref: `{request['worker_ref']}`",
        f"- Model Class: `{request['model_class']}`",
        f"- Result File: `{request['result_file']}`",
        "",
        "Execute only this assigned scope. Do not make control-plane decisions.",
        "Write one structured JSON result file to the exact result path shown above.",
        "",
        f"Summary: {task_summary}",
    ]
    if request["mode"] == "ideation":
        objective = load_handoff.get("objective_snapshot", {})
        baseline = state.get("baseline", {})
        lines.extend(
            [
                "",
                "## Campaign Context",
                f"- Goal: {load_handoff.get('goal', '')}",
                f"- Metric: `{objective.get('metric', '')}`",
                f"- Direction: `{objective.get('direction', '')}`",
                f"- Aggregation Method: `{objective.get('aggregation', {}).get('method', '')}`",
                f"- Aggregation Weights: `{json.dumps(objective.get('aggregation', {}).get('weights'))}`",
                f"- Aggregate Baseline: `{baseline.get('aggregate')}`",
                f"- Per-Dataset Baselines: `{json.dumps(baseline.get('by_dataset', {}), sort_keys=True)}`",
                f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`",
                f"- Completed Experiments: `{json.dumps(state.get('completed_experiments', []), sort_keys=True)}`",
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
            ]
        )
    elif request["mode"] == "maintenance":
        target_worktree = f".ml-metaopt/worktrees/{slot_id}"
        objective = load_handoff.get("objective_snapshot", {})
        lines.extend(
            [
                "",
                "## Campaign Context",
                f"- Goal: {load_handoff.get('goal', '')}",
                f"- Metric: `{objective.get('metric', '')}`",
                f"- Direction: `{objective.get('direction', '')}`",
                "",
                "## Target Worktree",
                f"- Path: `{target_worktree}`",
                "- Operate only within this isolated worktree. Do not modify the orchestrator working tree.",
                "",
                "## Output Mode",
                "This is a findings-only maintenance pass. Do not produce code changes or patch artifacts.",
                "Focus on identifying issues, risks, and improvement opportunities across these areas:",
                "- leakage audit",
                "- test gaps and determinism",
                "- pipeline correctness",
                "- data loading efficiency",
                "- code quality issues",
                "- profiling and speed risks",
                "",
                "## Patch Artifact Contract (for reference)",
                "If code-modifying output were requested, each patch artifact would require:",
                "- `producer_slot_id`: the dispatching slot ID",
                "- `purpose`: short description of the change",
                "- `patch_path`: path under `.ml-metaopt/artifacts/patches/`",
                "- `target_worktree`: the worktree path the patch applies to",
                "Patch artifacts are not produced in findings-only mode.",
                "",
                "## Output Schema",
                "- `slot_id`",
                "- `mode = \"maintenance\"`",
                "- `status`",
                "- `summary`",
                "- optional `findings` (list of finding strings)",
            ]
        )
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
    dispatch_policy = load_handoff["dispatch_policy"]
    proposal_policy = load_handoff["proposal_policy"]

    if _ready_for_selection(state, load_handoff):
        state["next_action"] = "select experiment"
        payload = {
            "schema_version": 1,
            "pool_status": "ready",
            "recommended_next_machine_state": "SELECT_EXPERIMENT",
            "active_background_slots": len([slot for slot in state["active_slots"] if slot["slot_class"] == "background"]),
            "launch_requests": [],
            "keep_running_slots": [slot["slot_id"] for slot in state["active_slots"] if slot["status"] == "running"],
            "harvest_candidates": [],
            "shortfall_reason": "",
            "summary": "proposal pool already satisfies selection gate",
        }
        if secondary:
            payload["recommended_next_machine_state"] = None
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    active_background = [slot for slot in state["active_slots"] if slot["slot_class"] == "background" and slot["status"] == "running"]
    needed = max(0, dispatch_policy["background_slots"] - len(active_background))
    launch_requests: list[dict[str, Any]] = []

    next_count = len(state["next_proposals"])
    current_iter = state["current_iteration"]
    saturated_this_iter = state["proposal_cycle"].get("pool_saturated_iteration") == current_iter
    use_maintenance = next_count >= proposal_policy["next_cap"] or saturated_this_iter
    mode = "maintenance" if use_maintenance else "ideation"
    worker_kind = "skill" if use_maintenance else "custom_agent"
    worker_ref = "repo-audit-refactor-optimize" if use_maintenance else "metaopt-ideation-worker"
    model_class = "general_worker"

    existing_slot_ids = {slot["slot_id"] for slot in state["active_slots"]}
    next_slot_num = 1
    for _ in range(needed):
        while f"bg-{next_slot_num}" in existing_slot_ids:
            next_slot_num += 1
        slot_id = f"bg-{next_slot_num}"
        existing_slot_ids.add(slot_id)
        result_file = str((Path(".ml-metaopt") / "worker-results" / f"{slot_id}.json"))
        task_file_rel = Path(".ml-metaopt") / "tasks" / f"{slot_id}.md"
        request = {
            "slot_class": "background",
            "mode": mode,
            "worker_ref": worker_ref,
            "model_class": model_class,
            "task_file": str(task_file_rel),
            "result_file": result_file,
        }
        launch_requests.append(request)
        task_path = tasks_dir / f"{slot_id}.md"
        task_path.write_text(_task_markdown(slot_id, request, load_handoff, state), encoding="utf-8")
        next_slot_num += 1

    state["proposal_cycle"]["current_pool_frozen"] = False
    if state["current_iteration"] == 1 and not state["proposal_cycle"]["cycle_id"]:
        state["proposal_cycle"]["cycle_id"] = "iter-1-cycle-1"
    state["next_action"] = "execute planned background work"

    payload = {
        "schema_version": 1,
        "pool_status": "building",
        "recommended_next_machine_state": "MAINTAIN_BACKGROUND_POOL",
        "active_background_slots": len([slot for slot in state["active_slots"] if slot["slot_class"] == "background"]),
        "launch_requests": launch_requests,
        "keep_running_slots": [slot["slot_id"] for slot in active_background],
        "harvest_candidates": [],
        "shortfall_reason": state["proposal_cycle"]["shortfall_reason"],
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
    slot_events_dir: Path,
    output_path: Path,
    secondary: bool = False,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    proposal_policy = load_handoff["proposal_policy"]
    sequence = _proposal_sequence(state)
    processed_slots: list[str] = []

    for slot in state["active_slots"]:
        slot_id = slot["slot_id"]
        event_path = slot_events_dir / f"{slot_id}.json"
        if not event_path.exists():
            continue
        slot_event = read_json(event_path)
        if slot_event.get("status") != "completed":
            continue
        result_path = worker_results_dir / f"{slot_id}.json"
        if not result_path.exists():
            continue
        result = read_json(result_path)
        processed_slots.append(slot_id)

        if slot["mode"] == "ideation" and result.get("status") == "completed":
            drift_fields = check_lane_drift("ideation", result)
            if drift_fields:
                state["status"] = "BLOCKED_PROTOCOL"
                state["machine_state"] = "BLOCKED_PROTOCOL"
                state["next_action"] = (
                    "protocol violation: ideation result contains semantic-lane "
                    f"fields {drift_fields}; manual intervention required"
                )
                payload = {
                    "schema_version": 1,
                    "pool_status": "blocked",
                    "recommended_next_machine_state": "BLOCKED_PROTOCOL",
                    "current_proposal_count": len(state["current_proposals"]),
                    "next_proposal_count": len(state["next_proposals"]),
                    "shortfall_reason": "",
                    "processed_slots": processed_slots,
                    "summary": (
                        f"ideation result from {slot_id} leaked semantic-lane "
                        f"fields: {drift_fields}"
                    ),
                    "warnings": [
                        f"lane drift detected in {slot_id}: {drift_fields}"
                    ],
                }
                persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
                return emit_handoff(
                    output_path,
                    payload,
                    handoff_type=_GATE_HANDOFF_TYPE,
                    control_agent=_CONTROL_AGENT,
                )
            candidates = result.get("proposal_candidates", [])
            for candidate in candidates:
                sequence += 1
                enriched = dict(candidate)
                enriched["proposal_id"] = f"{state['campaign_id']}-p{sequence}"
                enriched["source_slot_id"] = slot_id
                enriched["creation_iteration"] = state["current_iteration"]
                enriched["created_at"] = timestamp()
                destination = "current_proposals" if not state["proposal_cycle"]["current_pool_frozen"] else "next_proposals"
                state[destination].append(enriched)
            rounds = state["proposal_cycle"]["ideation_rounds_by_slot"]
            rounds[slot_id] = rounds.get(slot_id, 0) + 1
            if result.get("saturated"):
                state["proposal_cycle"]["pool_saturated_iteration"] = state["current_iteration"]

        elif slot["mode"] == "maintenance" and result.get("status") == "completed":
            state.setdefault("maintenance_summary", [])
            findings = result.get("findings", [])
            summary_text = result.get("summary", "")
            if findings or summary_text:
                state["maintenance_summary"].append({
                    "slot_id": slot_id,
                    "iteration": state["current_iteration"],
                    "summary": summary_text,
                    "findings": findings if isinstance(findings, list) else [],
                })

    if _ready_for_selection(state, load_handoff):
        state["proposal_cycle"]["shortfall_reason"] = ""
        state["next_action"] = "select experiment"
        recommended_next_machine_state = "SELECT_EXPERIMENT"
        pool_status = "ready"
        summary = "proposal pool satisfies selection gate"
    else:
        current_count = len(state["current_proposals"])
        rounds = state["proposal_cycle"]["ideation_rounds_by_slot"]
        if rounds and all(count >= 2 for count in rounds.values()) and current_count < proposal_policy["current_floor"]:
            state["proposal_cycle"]["shortfall_reason"] = "floor_not_met"
        else:
            state["proposal_cycle"]["shortfall_reason"] = "not_enough_proposals"
        state["next_action"] = "plan more background work"
        recommended_next_machine_state = "MAINTAIN_BACKGROUND_POOL"
        pool_status = "building"
        summary = "proposal pool still below threshold"

    payload = {
        "schema_version": 1,
        "pool_status": pool_status,
        "recommended_next_machine_state": recommended_next_machine_state,
        "current_proposal_count": len(state["current_proposals"]),
        "next_proposal_count": len(state["next_proposals"]),
        "shortfall_reason": state["proposal_cycle"]["shortfall_reason"],
        "processed_slots": processed_slots,
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
    load_handoff = read_json(Path(args.load_handoff))
    state_path = Path(args.state_path)
    tasks_dir = Path(args.tasks_dir)
    worker_results_dir = Path(args.worker_results_dir)
    slot_events_dir = Path(args.slot_events_dir)
    output_path = Path(args.output)

    if args.mode == "plan_background_work":
        payload = _plan_background_work(load_handoff, state_path, tasks_dir, output_path, secondary=args.secondary)
    else:
        payload = _gate_background_work(load_handoff, state_path, worker_results_dir, slot_events_dir, output_path, secondary=args.secondary)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

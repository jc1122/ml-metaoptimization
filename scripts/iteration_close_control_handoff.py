from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import re
from pathlib import Path
from typing import Any

from _handoff_utils import (
    emit_handoff,
    load_campaign_handoff_is_ready,
    persist_state_handoff,
    read_json,
    timestamp,
    write_json,
)

_CONTROL_AGENT = "metaopt-iteration-close-control"
_PLAN_HANDOFF_TYPE = "iteration_close.plan_roll_iteration"
_GATE_HANDOFF_TYPE = "iteration_close.gate_roll_iteration"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Steps 12/13 iteration-close control handoffs.")
    parser.add_argument("--mode", required=True, choices=("plan_roll_iteration", "gate_roll_iteration"))
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--worker-results-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--apply-state",
        action="store_true",
        default=False,
        help="Test/orchestrator harness mode: apply the computed state_patch to state-path.",
    )
    return parser.parse_args()


def _runtime_error(
    output_path: Path,
    handoff_type: str,
    recovery_action: str,
    summary: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "continue_campaign": None,
        "stop_reason": "",
        "recommended_next_machine_state": None,
        "recovery_action": recovery_action,
        "iteration_report": None,
        "state_patch": None,
        "warnings": warnings or [],
        "summary": summary,
    }
    return emit_handoff(output_path, payload, handoff_type=handoff_type, control_agent=_CONTROL_AGENT)


def _load_inputs(load_handoff_path: Path, state_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    try:
        load_handoff = read_json(load_handoff_path)
    except Exception as exc:
        return None, None, {"action": "repair or replace load_campaign.latest.json", "summary": "load handoff unreadable", "warnings": [str(exc)]}
    if not load_campaign_handoff_is_ready(load_handoff):
        return None, None, {"action": "repair or replace load_campaign.latest.json", "summary": "load handoff invalid", "warnings": []}
    try:
        state = read_json(state_path)
    except Exception as exc:
        return load_handoff, None, {"action": "repair or replace .ml-metaopt/state.json", "summary": "state unreadable", "warnings": [str(exc)]}
    if not isinstance(state, dict):
        return load_handoff, None, {"action": "repair or replace .ml-metaopt/state.json", "summary": "state invalid", "warnings": []}
    return load_handoff, state, None


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


def _validate_rollover_result(payload: Any) -> list[str]:
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return ["rollover worker result must be a JSON object"]
    required = ("filtered_proposals", "merged_proposals", "needs_fresh_ideation", "summary")
    for key in required:
        if key not in payload:
            warnings.append(f"missing field: {key}")
    filtered = payload.get("filtered_proposals")
    if "filtered_proposals" in payload and not isinstance(filtered, list):
        warnings.append("filtered_proposals must be a list")
    merged = payload.get("merged_proposals")
    if "merged_proposals" in payload and not isinstance(merged, list):
        warnings.append("merged_proposals must be a list")
    return warnings


def _stop_reason(state: dict[str, Any], stop_conditions: dict[str, Any], load_handoff: dict[str, Any] | None = None) -> str:
    baseline = state.get("baseline") or {}
    baseline_value = baseline.get("value")
    direction = state["objective_snapshot"]["direction"]
    target = stop_conditions.get("target_metric")
    if baseline_value is not None and target is not None:
        if (direction == "minimize" and baseline_value <= target) or (direction == "maximize" and baseline_value >= target):
            return "target_metric"
    if state["current_iteration"] >= stop_conditions["max_iterations"]:
        return "max_iterations"
    if state["no_improve_iterations"] >= stop_conditions["max_no_improve_iterations"]:
        return "max_no_improve_iterations"
    # Budget cap check
    max_budget = (load_handoff or {}).get("compute", {}).get("max_budget_usd")
    if max_budget is not None:
        total_spend = sum(
            entry.get("spend_usd", 0.0)
            for entry in state.get("completed_iterations", [])
        )
        if total_spend >= max_budget:
            return "budget_exhausted"
    return ""


def _iteration_report(iteration: int, state: dict[str, Any]) -> str:
    metric = state["objective_snapshot"]["metric"]
    baseline = state.get("baseline") or {}
    return f"=== Iteration {iteration} Report ===\nMetric: {metric}\nBaseline value: {baseline.get('value')}\nNo-improve iterations: {state['no_improve_iterations']}\nNext action: {state['next_action']}"


def _plan_roll_iteration(load_handoff: dict[str, Any], state_path: Path, tasks_dir: Path, output_path: Path) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)

    iteration = state["current_iteration"]
    task_path = tasks_dir / f"rollover-iter-{iteration}.md"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(f"# Rollover Task: iteration {iteration}\n", encoding="utf-8")
    state["next_action"] = "run proposal rollover"

    rollover_task_file = str(Path(".ml-metaopt") / "tasks" / f"rollover-iter-{iteration}.md")
    rollover_result_file = str(Path(".ml-metaopt") / "worker-results" / f"rollover-iter-{iteration}.json")
    payload = {
        "schema_version": 1,
        "continue_campaign": None,
        "stop_reason": "",
        "recommended_next_machine_state": "ROLL_ITERATION",
        "iteration_report": None,
        "launch_requests": [
            {
                "slot_class": "auxiliary",
                "mode": "analysis",
                "worker_ref": "metaopt-analysis-worker",
                "model_class": "strong_reasoner",
                "task_file": rollover_task_file,
                "result_file": rollover_result_file,
            },
        ],
        "warnings": [],
        "summary": "iteration rollover worker is ready to run",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _gate_roll_iteration(load_handoff: dict[str, Any], state_path: Path, worker_results_dir: Path, output_path: Path) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    iteration = state["current_iteration"]
    result_path = worker_results_dir / f"rollover-iter-{iteration}.json"
    if not result_path.exists():
        return _runtime_error(
            output_path,
            _GATE_HANDOFF_TYPE,
            "stage rollover worker output before gating",
            "rollover worker result missing",
        )

    rollover_result = read_json(result_path)
    rollover_warnings = _validate_rollover_result(rollover_result)
    if rollover_warnings:
        return _runtime_error(
            output_path,
            _GATE_HANDOFF_TYPE,
            "repair rollover worker output",
            "rollover worker output violates the iteration-close contract shape",
            rollover_warnings,
        )

    filtered = rollover_result.get("filtered_proposals", [])
    merged = rollover_result.get("merged_proposals", [])
    sequence = _proposal_sequence(state)
    current_pool = list(filtered)
    next_iteration = iteration + 1
    for candidate in merged:
        sequence += 1
        enriched = dict(candidate)
        enriched["proposal_id"] = f"{state['campaign_id']}-p{sequence}"
        enriched["source_slot_id"] = "rollover"
        enriched["creation_iteration"] = next_iteration
        enriched["created_at"] = timestamp()
        current_pool.append(enriched)

    state["current_proposals"] = current_pool
    state["next_proposals"] = []

    stop_reason = _stop_reason(state, load_handoff["stop_conditions"], load_handoff)
    continue_campaign = not bool(stop_reason)

    if continue_campaign:
        state["current_iteration"] = next_iteration
        state["proposal_cycle"] = {
            "cycle_id": f"iter-{next_iteration}-cycle-1",
            "current_pool_frozen": False,
        }
        state["next_action"] = "maintain background pool"
        next_state = "IDEATE"
    elif stop_reason == "budget_exhausted":
        max_budget = load_handoff.get("compute", {}).get("max_budget_usd", "?")
        total_spend = sum(
            entry.get("spend_usd", 0.0)
            for entry in state.get("completed_iterations", [])
        )
        state["next_action"] = f"Budget cap exceeded: {total_spend} USD spent of {max_budget} USD limit. Increase compute.max_budget_usd or reduce num_sweep_agents."
        next_state = "BLOCKED_CONFIG"
    else:
        state["next_action"] = "emit final report and remove orchestration hook"
        next_state = "COMPLETE"

    state["selected_sweep"] = None
    state["current_sweep"] = None
    report = _iteration_report(iteration, state)

    directives: list[dict[str, Any]] = [
        {
            "action": "emit_iteration_report",
            "reason": "iteration rollover complete; publish iteration report",
            "report_type": "iteration",
            "iteration": iteration,
        },
    ]
    if not continue_campaign:
        directives.extend([
            {
                "action": "remove_agents_hook",
                "reason": "campaign complete; orchestration hook no longer needed",
                "agents_path": "AGENTS.md",
            },
            {
                "action": "emit_final_report",
                "reason": "campaign complete; produce final summary",
                "report_type": "final",
            },
        ])

    payload = {
        "schema_version": 1,
        "continue_campaign": continue_campaign,
        "stop_reason": stop_reason,
        "recommended_next_machine_state": next_state,
        "iteration_report": report,
        "directives": directives,
        "warnings": [],
        "summary": "rollover semantics applied",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_GATE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    if args.apply_state:
        os.environ["METAOPT_APPLY_STATE_HANDOFF"] = "1"
    load_handoff, _, error = _load_inputs(Path(args.load_handoff), Path(args.state_path))
    if error is not None:
        payload = _runtime_error(
            Path(args.output),
            {
                "plan_roll_iteration": _PLAN_HANDOFF_TYPE,
                "gate_roll_iteration": _GATE_HANDOFF_TYPE,
            }[args.mode],
            error["action"],
            error["summary"],
            error["warnings"],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.mode == "plan_roll_iteration":
        payload = _plan_roll_iteration(load_handoff, Path(args.state_path), Path(args.tasks_dir), Path(args.output))
    else:
        payload = _gate_roll_iteration(load_handoff, Path(args.state_path), Path(args.worker_results_dir), Path(args.output))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

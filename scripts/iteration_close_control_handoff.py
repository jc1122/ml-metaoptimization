from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import re
from datetime import datetime, timezone
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
_QUIESCE_HANDOFF_TYPE = "iteration_close.quiesce_slots"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Steps 12/13 iteration-close control handoffs.")
    parser.add_argument("--mode", required=True, choices=("plan_roll_iteration", "gate_roll_iteration", "quiesce_slots"))
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--worker-results-dir", required=True)
    parser.add_argument("--executor-events-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--apply-state",
        action="store_true",
        default=False,
        help="Test/orchestrator harness mode: apply the computed state_patch to state-path.",
    )
    return parser.parse_args()


def _read_json(path: Path) -> Any:
    return read_json(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def _timestamp() -> str:
    return timestamp()


def _runtime_error(
    output_path: Path,
    handoff_type: str,
    recovery_action: str,
    summary: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "result_file": None,
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


def _blocked_protocol(
    output_path: Path,
    state_path: Path,
    state: dict[str, Any],
    *,
    handoff_type: str,
    summary: str,
    warnings: list[str],
    next_action: str,
) -> dict[str, Any]:
    previous_state = deepcopy(state)
    state["status"] = "BLOCKED_PROTOCOL"
    state["machine_state"] = "BLOCKED_PROTOCOL"
    state["next_action"] = next_action

    payload = {
        "schema_version": 1,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "result_file": None,
        "continue_campaign": False,
        "stop_reason": "protocol_violation",
        "recommended_next_machine_state": "BLOCKED_PROTOCOL",
        "iteration_report": state.get("last_iteration_report"),
        "pre_launch_directives": [
            {
                "action": "remove_agents_hook",
                "reason": "protocol blocked; orchestration hook no longer needed",
                "agents_path": "AGENTS.md",
            }
        ],
        "post_launch_directives": [],
        "warnings": warnings,
        "summary": summary,
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=handoff_type, control_agent=_CONTROL_AGENT)


def _load_inputs(load_handoff_path: Path, state_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    try:
        load_handoff = _read_json(load_handoff_path)
    except Exception as exc:
        return None, None, {"action": "repair or replace load_campaign.latest.json", "summary": "load handoff unreadable", "warnings": [str(exc)]}
    if not load_campaign_handoff_is_ready(load_handoff):
        return None, None, {"action": "repair or replace load_campaign.latest.json", "summary": "load handoff invalid", "warnings": []}

    try:
        state = _read_json(state_path)
    except Exception as exc:
        return load_handoff, None, {"action": "repair or replace .ml-metaopt/state.json", "summary": "state unreadable", "warnings": [str(exc)]}
    if not isinstance(state, dict):
        return load_handoff, None, {"action": "repair or replace .ml-metaopt/state.json", "summary": "state invalid", "warnings": []}
    return load_handoff, state, None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
    elif isinstance(filtered, list):
        for index, proposal in enumerate(filtered):
            if not isinstance(proposal, dict):
                warnings.append(f"filtered_proposals[{index}] must be an object")
                continue
            for field in ("proposal_id", "source_slot_id", "creation_iteration", "created_at", "title", "rationale", "expected_impact", "target_area"):
                if field not in proposal:
                    warnings.append(f"filtered_proposals[{index}] missing field: {field}")

    merged = payload.get("merged_proposals")
    if "merged_proposals" in payload and not isinstance(merged, list):
        warnings.append("merged_proposals must be a list")
    elif isinstance(merged, list):
        for index, proposal in enumerate(merged):
            if not isinstance(proposal, dict):
                warnings.append(f"merged_proposals[{index}] must be an object")
                continue
            for field in ("title", "rationale", "expected_impact", "target_area"):
                if field not in proposal:
                    warnings.append(f"merged_proposals[{index}] missing field: {field}")

    needs_fresh_ideation = payload.get("needs_fresh_ideation")
    if "needs_fresh_ideation" in payload and not isinstance(needs_fresh_ideation, bool):
        warnings.append("needs_fresh_ideation must be a boolean")

    summary = payload.get("summary")
    if "summary" in payload and not isinstance(summary, dict):
        warnings.append("summary must be an object")
    elif isinstance(summary, dict):
        for field in ("carried_over", "discarded", "merged", "final_pool_size"):
            if field not in summary:
                warnings.append(f"summary missing field: {field}")
            elif not _is_number(summary[field]):
                warnings.append(f"summary.{field} must be numeric")

    return warnings


def _validate_quiesce_event(payload: Any) -> list[str]:
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return ["quiesce executor event must be a JSON object"]

    for field in ("finished_slots", "canceled_slots", "drain_duration_seconds", "maintenance_apply_results", "summary"):
        if field not in payload:
            warnings.append(f"missing field: {field}")

    if "finished_slots" in payload and not isinstance(payload.get("finished_slots"), list):
        warnings.append("finished_slots must be a list")
    if "canceled_slots" in payload and not isinstance(payload.get("canceled_slots"), list):
        warnings.append("canceled_slots must be a list")
    if "maintenance_apply_results" in payload and not isinstance(payload.get("maintenance_apply_results"), list):
        warnings.append("maintenance_apply_results must be a list")
    if "drain_duration_seconds" in payload and not _is_number(payload.get("drain_duration_seconds")):
        warnings.append("drain_duration_seconds must be numeric")
    if "summary" in payload and (not isinstance(payload.get("summary"), str) or not payload["summary"]):
        warnings.append("summary must be a non-empty string")

    continue_campaign = payload.get("continue_campaign")
    blocked_protocol = payload.get("blocked_protocol", False)
    if continue_campaign is not None and not isinstance(continue_campaign, bool):
        warnings.append("continue_campaign must be a boolean when present")
    if "blocked_protocol" in payload and not isinstance(blocked_protocol, bool):
        warnings.append("blocked_protocol must be a boolean when present")

    if continue_campaign is None and "blocked_protocol" not in payload:
        warnings.append("quiesce event must explicitly choose continue_campaign or blocked_protocol")
    if continue_campaign is True and blocked_protocol:
        warnings.append("quiesce event cannot set both continue_campaign and blocked_protocol")
    if continue_campaign is False and not blocked_protocol:
        stop_reason = payload.get("stop_reason")
        if not isinstance(stop_reason, str) or not stop_reason:
            warnings.append("stop_reason must be a non-empty string when stopping")

    return warnings


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
    selected = state.get("selected_experiment")
    if isinstance(selected, dict):
        proposal_id = selected.get("proposal_id")
        if isinstance(proposal_id, str):
            match = pattern.fullmatch(proposal_id)
            if match:
                max_seen = max(max_seen, int(match.group(1)))
    return max_seen


def _rollover_task_markdown(iteration: int, state: dict[str, Any], load_handoff: dict[str, Any]) -> str:
    objective = state["objective_snapshot"]
    analysis_summary = state["selected_experiment"]["analysis_summary"]
    return "\n".join(
        [
            f"# Rollover Task: iteration {iteration}",
            "",
            f"- Iteration: `{iteration}`",
            "- Worker Kind: `custom_agent`",
            "- Worker Ref: `metaopt-rollover-worker`",
            "- Model Class: `strong_reasoner`",
            f"- Result File: `.ml-metaopt/worker-results/rollover-iter-{iteration}.json`",
            "",
            "## Objective Context",
            f"- Metric: `{objective.get('metric', '')}`",
            f"- Direction: `{objective.get('direction', '')}`",
            f"- Aggregation Method: `{objective.get('aggregation', {}).get('method', '')}`",
            f"- Aggregation Weights: `{json.dumps(objective.get('aggregation', {}).get('weights'), sort_keys=True)}`",
            "",
            "## Proposal Context",
            f"- Next Proposals: `{json.dumps(state.get('next_proposals', []), sort_keys=True)}`",
            f"- Proposal Policy: `{json.dumps(load_handoff.get('proposal_policy', {}), sort_keys=True)}`",
            "",
            "## Analysis Context",
            f"- Analysis Summary: `{json.dumps(analysis_summary, sort_keys=True)}`",
            f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`",
            f"- Completed Experiments: `{json.dumps(state.get('completed_experiments', []), sort_keys=True)}`",
            "",
            "## Stop Progress Context",
            f"- Current Iteration: `{state.get('current_iteration')}`",
            f"- No Improve Iterations: `{state.get('no_improve_iterations')}`",
            f"- Stop Conditions: `{json.dumps(load_handoff.get('stop_conditions', {}), sort_keys=True)}`",
            "",
            "Execute only this assigned scope. Do not make control-plane decisions.",
            "Do not launch subagents or mutate `.ml-metaopt/state.json`.",
            "Return filtered proposals, merged proposals, pool health, and rollover summary statistics.",
            "",
            "Expected JSON fields:",
            "- `filtered_proposals`",
            "- `merged_proposals`",
            "- `needs_fresh_ideation`",
            "- `summary`",
        ]
    ) + "\n"


def _iteration_report(
    completed_iteration: int,
    state: dict[str, Any],
    analysis_summary: dict[str, Any],
    batch_id: str,
    baseline_before: float,
    baseline_after: float,
    delta: float,
) -> str:
    metric = state["objective_snapshot"]["metric"]
    per_dataset = " ".join(f"{key}={value}" for key, value in state["baseline"]["by_dataset"].items())
    learnings = "; ".join(analysis_summary.get("learnings", [])) or "none"
    maintenance_entries = state.get("maintenance_summary") or []
    maintenance_text = (
        "; ".join(entry.get("summary", "") for entry in maintenance_entries if entry.get("summary"))
        or "none"
    )
    return "\n".join(
        [
            f"=== Iteration {completed_iteration} Report ===",
            f"Experiment batch:       {batch_id}",
            f"Baseline before:        {metric} = {baseline_before}",
            f"Baseline after:         {metric} = {baseline_after} ({delta:+.4f})",
            f"Per-dataset scores:     {per_dataset}",
            f"Key learnings:          {learnings}",
            f"Carry-over proposals:   {len(state['current_proposals'])}",
            f"Maintenance work done:  {maintenance_text}",
            f"Next action:            {state['next_action']}",
        ]
    )


def _stop_reason(state: dict[str, Any], stop_conditions: dict[str, Any]) -> str:
    aggregate = state["baseline"]["aggregate"]
    direction = state["objective_snapshot"]["direction"]
    target = stop_conditions["target_metric"]
    if (direction == "minimize" and aggregate <= target) or (direction == "maximize" and aggregate >= target):
        return "target_metric"
    if state["current_iteration"] >= stop_conditions["max_iterations"]:
        return "max_iterations"
    if state["no_improve_iterations"] >= stop_conditions["max_no_improve_iterations"]:
        return "max_no_improve_iterations"
    started_at = state.get("campaign_started_at")
    max_hours = stop_conditions.get("max_wallclock_hours")
    if started_at and max_hours is not None:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        elapsed_hours = (datetime.now(timezone.utc) - start).total_seconds() / 3600
        if elapsed_hours >= max_hours:
            return "max_wallclock_hours"
    return ""


def _plan_roll_iteration(load_handoff: dict[str, Any], state_path: Path, tasks_dir: Path, output_path: Path) -> dict[str, Any]:
    state = _read_json(state_path)
    previous_state = deepcopy(state)
    selected = state.get("selected_experiment")
    if not isinstance(selected, dict) or not isinstance(selected.get("analysis_summary"), dict):
        return _runtime_error(
            output_path,
            _PLAN_HANDOFF_TYPE,
            "stage selected experiment analysis before rollover",
            "analysis summary missing",
        )

    iteration = state["current_iteration"]
    task_path = tasks_dir / f"rollover-iter-{iteration}.md"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(_rollover_task_markdown(iteration, state, load_handoff), encoding="utf-8")
    state["machine_state"] = "ROLL_ITERATION"
    state["next_action"] = "run proposal rollover"

    rollover_task_file = str(Path(".ml-metaopt") / "tasks" / f"rollover-iter-{iteration}.md")
    rollover_result_file = str(Path(".ml-metaopt") / "worker-results" / f"rollover-iter-{iteration}.json")
    payload = {
        "schema_version": 1,
        "worker_kind": "custom_agent",
        "worker_ref": "metaopt-rollover-worker",
        "task_file": rollover_task_file,
        "result_file": rollover_result_file,
        "continue_campaign": None,
        "stop_reason": "",
        "recommended_next_machine_state": "ROLL_ITERATION",
        "iteration_report": None,
        "launch_requests": [
            {
                "worker_ref": "metaopt-rollover-worker",
                "model_class": "strong_reasoner",
                "task_file": rollover_task_file,
                "result_file": rollover_result_file,
            },
        ],
        "pre_launch_directives": [],
        "post_launch_directives": [],
        "warnings": [],
        "summary": "iteration rollover worker is ready to run",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _gate_roll_iteration(load_handoff: dict[str, Any], state_path: Path, worker_results_dir: Path, output_path: Path) -> dict[str, Any]:
    state = _read_json(state_path)
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

    rollover_result = _read_json(result_path)
    rollover_warnings = _validate_rollover_result(rollover_result)
    if rollover_warnings:
        return _blocked_protocol(
            output_path,
            state_path,
            state,
            handoff_type=_GATE_HANDOFF_TYPE,
            summary="rollover worker output violates the iteration-close contract shape",
            warnings=rollover_warnings,
            next_action="repair rollover worker output and resume from the preserved state",
        )
    filtered = rollover_result.get("filtered_proposals", [])
    merged = rollover_result.get("merged_proposals", [])

    continue_campaign = True
    analysis_summary = state["selected_experiment"]["analysis_summary"]
    baseline_before = state["baseline"]["aggregate"] - analysis_summary["delta"]
    baseline_after = state["baseline"]["aggregate"]
    delta = analysis_summary["delta"]

    sequence = _proposal_sequence(state)
    current_pool = list(filtered)
    next_iteration = iteration + 1
    for candidate in merged:
        sequence += 1
        enriched = dict(candidate)
        enriched["proposal_id"] = f"{state['campaign_id']}-p{sequence}"
        enriched["source_slot_id"] = "rollover"
        enriched["creation_iteration"] = next_iteration
        enriched["created_at"] = _timestamp()
        current_pool.append(enriched)

    state["current_proposals"] = current_pool
    state["next_proposals"] = []
    stop_reason = _stop_reason(state, load_handoff["stop_conditions"])
    if stop_reason:
        continue_campaign = False
    else:
        state["current_iteration"] = next_iteration

    state["selected_experiment"] = None
    state["machine_state"] = "QUIESCE_SLOTS"
    state["next_action"] = "drain or cancel active slots"
    state["last_iteration_report"] = _iteration_report(
        iteration,
        state,
        analysis_summary,
        state["completed_experiments"][-1]["batch_id"],
        baseline_before,
        baseline_after,
        delta,
    )
    active_slot_ids = [
        slot["slot_id"]
        for slot in state.get("active_slots", [])
        if isinstance(slot, dict) and isinstance(slot.get("slot_id"), str) and slot["slot_id"]
    ]

    payload = {
        "schema_version": 1,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "result_file": str(Path(".ml-metaopt") / "worker-results" / f"rollover-iter-{iteration}.json"),
        "continue_campaign": continue_campaign,
        "stop_reason": stop_reason,
        "recommended_next_machine_state": "QUIESCE_SLOTS",
        "iteration_report": state["last_iteration_report"],
        "pre_launch_directives": [
            {
                "action": "emit_iteration_report",
                "reason": "iteration rollover complete; publish iteration report",
                "report_type": "iteration",
                "iteration": iteration,
            },
            {
                "action": "drain_slots",
                "reason": "drain active background slots before next iteration or shutdown",
                "drain_window_seconds": 60,
                "quiesce_event_path": str(
                    Path(".ml-metaopt") / "executor-events" / f"quiesce-slots-iter-{next_iteration if not stop_reason else iteration}.json"
                ),
            },
            {
                "action": "cancel_slots",
                "reason": "cancel slots that cannot be drained within timeout",
                "slot_ids": active_slot_ids,
            },
        ],
        "post_launch_directives": [],
        "warnings": [],
        "summary": "rollover semantics applied and quiesce preparation is complete",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_GATE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _quiesce_slots(state_path: Path, executor_events_dir: Path, output_path: Path) -> dict[str, Any]:
    state = _read_json(state_path)
    previous_state = deepcopy(state)
    iteration = state["current_iteration"]
    event_path = executor_events_dir / f"quiesce-slots-iter-{iteration}.json"
    if not event_path.exists():
        return _runtime_error(
            output_path,
            _QUIESCE_HANDOFF_TYPE,
            "stage quiesce executor output before gating",
            "quiesce event missing",
        )
    event = _read_json(event_path)
    event_warnings = _validate_quiesce_event(event)
    if event_warnings:
        return _blocked_protocol(
            output_path,
            state_path,
            state,
            handoff_type=_QUIESCE_HANDOFF_TYPE,
            summary="quiesce executor output violates the iteration-close contract shape",
            warnings=event_warnings,
            next_action="repair quiesce executor output and resume from the preserved state",
        )

    if state.get("local_changeset") is not None:
        state["local_changeset"]["apply_results"].extend(event.get("maintenance_apply_results", []))
    state["active_slots"] = []

    if event.get("continue_campaign"):
        state["status"] = "RUNNING"
        state["machine_state"] = "MAINTAIN_BACKGROUND_POOL"
        state["next_action"] = "maintain background slot pool"
        next_state = "MAINTAIN_BACKGROUND_POOL"
        cleanup_directives: list[dict[str, str]] = []
    elif event.get("blocked_protocol"):
        state["status"] = "BLOCKED_PROTOCOL"
        state["machine_state"] = "BLOCKED_PROTOCOL"
        state["next_action"] = "protocol cannot represent the next semantic step; manual intervention required"
        next_state = "BLOCKED_PROTOCOL"
        cleanup_directives = [
            {
                "action": "remove_agents_hook",
                "reason": "protocol blocked; orchestration hook no longer needed",
                "agents_path": "AGENTS.md",
            },
        ]
    else:
        state["status"] = "COMPLETE"
        state["machine_state"] = "COMPLETE"
        state["next_action"] = "emit final report and remove orchestration hook"
        next_state = "COMPLETE"
        cleanup_directives = [
            {
                "action": "remove_agents_hook",
                "reason": "campaign complete; orchestration hook no longer needed",
                "agents_path": "AGENTS.md",
            },
            {
                "action": "delete_state_file",
                "reason": "campaign complete; state file no longer needed",
                "state_path": ".ml-metaopt/state.json",
            },
            {
                "action": "emit_final_report",
                "reason": "campaign complete; produce final summary",
                "report_type": "final",
            },
        ]

    payload = {
        "schema_version": 1,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "result_file": str(Path(".ml-metaopt") / "executor-events" / f"quiesce-slots-iter-{iteration}.json"),
        "continue_campaign": bool(event.get("continue_campaign")),
        "stop_reason": event.get("stop_reason", ""),
        "recommended_next_machine_state": next_state,
        "iteration_report": state.get("last_iteration_report"),
        "pre_launch_directives": cleanup_directives,
        "post_launch_directives": [],
        "warnings": [],
        "summary": event.get("summary", "quiesce results integrated"),
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_QUIESCE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


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
                "quiesce_slots": _QUIESCE_HANDOFF_TYPE,
            }[args.mode],
            error["action"],
            error["summary"],
            error["warnings"],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.mode == "plan_roll_iteration":
        payload = _plan_roll_iteration(load_handoff, Path(args.state_path), Path(args.tasks_dir), Path(args.output))
    elif args.mode == "gate_roll_iteration":
        payload = _gate_roll_iteration(load_handoff, Path(args.state_path), Path(args.worker_results_dir), Path(args.output))
    else:
        payload = _quiesce_slots(Path(args.state_path), Path(args.executor_events_dir), Path(args.output))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

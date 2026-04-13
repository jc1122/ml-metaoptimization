from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
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

_CONTROL_AGENT = "metaopt-remote-execution-control"
_GATE_SANITY_HANDOFF_TYPE = "remote_execution.gate_local_sanity"
_PLAN_LAUNCH_HANDOFF_TYPE = "remote_execution.plan_launch"
_POLL_SWEEP_HANDOFF_TYPE = "remote_execution.poll_sweep"
_ANALYZE_HANDOFF_TYPE = "remote_execution.analyze"

_MODES = ("gate_local_sanity", "plan_launch", "poll_sweep", "analyze")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate remote execution control handoffs (v4: WandB Sweeps + SkyPilot).")
    parser.add_argument("--mode", required=True, choices=_MODES)
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


def _gate_local_sanity(
    load_handoff: dict[str, Any],
    state_path: Path,
    executor_events_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    iteration = state["current_iteration"]
    smoke_result_path = executor_events_dir / f"smoke-test-iter-{iteration}.json"
    if not smoke_result_path.exists():
        smoke_command = load_handoff.get("project", {}).get("smoke_test_command", "")
        result_file = f".ml-metaopt/worker-results/smoke-test-iter-{iteration}.json"
        state["next_action"] = "run smoke test"
        payload = {
            "schema_version": 1,
            "recommended_next_machine_state": "LAUNCH_SWEEP",
            "directives": [
                {
                    "action": "run_smoke_test",
                    "reason": "smoke test result not found; dispatching smoke test",
                    "command": smoke_command,
                    "result_file": result_file,
                },
            ],
            "warnings": [],
            "summary": "run_smoke_test directive emitted",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_GATE_SANITY_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    smoke_result = read_json(smoke_result_path)
    exit_code = smoke_result.get("exit_code", 1)
    timed_out = smoke_result.get("timed_out", False)

    if timed_out or exit_code != 0:
        state["next_action"] = "smoke test failed"
        payload = {
            "schema_version": 1,
            "recommended_next_machine_state": "FAILED",
            "warnings": [],
            "summary": f"smoke test {'timed out' if timed_out else f'failed with exit code {exit_code}'}",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_GATE_SANITY_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    state["next_action"] = "launch sweep"
    payload = {
        "schema_version": 1,
        "recommended_next_machine_state": "LAUNCH_SWEEP",
        "warnings": [],
        "summary": "smoke test passed; ready to launch sweep",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_GATE_SANITY_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _plan_launch(
    load_handoff: dict[str, Any],
    state_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    selected_sweep = state.get("selected_sweep")
    if not isinstance(selected_sweep, dict) or "sweep_config" not in selected_sweep:
        return _runtime_error(output_path, _PLAN_LAUNCH_HANDOFF_TYPE, "persist selected_sweep before launch", "selected_sweep missing or invalid")

    sweep_config = selected_sweep["sweep_config"]
    iteration = state["current_iteration"]
    result_file = f".ml-metaopt/worker-results/launch-sweep-iter-{iteration}.json"

    compute = load_handoff.get("compute", {})
    sky_task_spec = {
        "provider": compute.get("provider", "vast_ai"),
        "accelerator": compute.get("accelerator", "A100:1"),
        "num_sweep_agents": compute.get("num_sweep_agents", 4),
        "idle_timeout_minutes": compute.get("idle_timeout_minutes", 15),
        "max_budget_usd": compute.get("max_budget_usd", 10),
    }

    state["current_sweep"] = {
        "sweep_id": None,
        "sweep_url": None,
        "sky_job_ids": [],
        "launched_at": None,
        "cumulative_spend_usd": 0.0,
        "best_run_id": None,
        "best_metric_value": None,
    }
    state["next_action"] = "execute launch sweep directive"
    payload = {
        "schema_version": 1,
        "recommended_next_machine_state": "LAUNCH_SWEEP",
        "directives": [
            {
                "action": "launch_sweep",
                "reason": "selected sweep config validated; launching WandB sweep via SkyPilot",
                "sweep_config": sweep_config,
                "sky_task_spec": sky_task_spec,
                "result_file": result_file,
            },
        ],
        "warnings": [],
        "summary": "launch_sweep directive emitted",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_PLAN_LAUNCH_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _poll_sweep(
    load_handoff: dict[str, Any],
    state_path: Path,
    executor_events_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    current_sweep = state.get("current_sweep")
    if not isinstance(current_sweep, dict):
        return _runtime_error(output_path, _POLL_SWEEP_HANDOFF_TYPE, "launch sweep before polling", "current_sweep missing")

    sweep_id = current_sweep.get("sweep_id", "")
    sky_job_ids = current_sweep.get("sky_job_ids", [])
    iteration = state["current_iteration"]

    poll_result_path = executor_events_dir / f"poll-sweep-iter-{iteration}.json"
    if not poll_result_path.exists():
        result_file = f".ml-metaopt/worker-results/poll-sweep-iter-{iteration}.json"
        state["next_action"] = "poll WandB sweep status"
        payload = {
            "schema_version": 1,
            "recommended_next_machine_state": None,
            "directives": [
                {
                    "action": "poll_sweep",
                    "reason": "checking sweep status",
                    "sweep_id": sweep_id,
                    "sky_job_ids": sky_job_ids,
                    "result_file": result_file,
                },
            ],
            "warnings": [],
            "summary": "poll_sweep directive emitted",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_POLL_SWEEP_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    poll_result = read_json(poll_result_path)
    sweep_status = poll_result.get("sweep_status", "")

    if "cumulative_spend_usd" in poll_result:
        current_sweep["cumulative_spend_usd"] = poll_result["cumulative_spend_usd"]

    if sweep_status == "running":
        state["next_action"] = "poll WandB sweep status"
        payload = {
            "schema_version": 1,
            "recommended_next_machine_state": None,
            "warnings": [],
            "summary": "sweep is still running",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_POLL_SWEEP_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    if sweep_status == "completed":
        state["next_action"] = "analyze sweep results"
        payload = {
            "schema_version": 1,
            "recommended_next_machine_state": "ANALYZE",
            "warnings": [],
            "summary": "sweep completed; advancing to analysis",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_POLL_SWEEP_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    if sweep_status == "budget_exceeded":
        state["next_action"] = "sweep budget exceeded"
        payload = {
            "schema_version": 1,
            "recommended_next_machine_state": "BLOCKED_CONFIG",
            "warnings": ["budget exceeded"],
            "summary": "sweep budget exceeded",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_POLL_SWEEP_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    state["next_action"] = "sweep failed"
    payload = {
        "schema_version": 1,
        "recommended_next_machine_state": "FAILED",
        "warnings": [],
        "summary": f"sweep failed with status: {sweep_status}",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_POLL_SWEEP_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _analyze(
    load_handoff: dict[str, Any],
    state_path: Path,
    tasks_dir: Path,
    worker_results_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    iteration = state["current_iteration"]
    analysis_result_file = f"sweep-analysis-iter-{iteration}.json"
    analysis_result_path = worker_results_dir / analysis_result_file

    if not analysis_result_path.exists():
        task_file = f".ml-metaopt/tasks/sweep-analysis-iter-{iteration}.md"
        result_file = f".ml-metaopt/worker-results/{analysis_result_file}"
        state["next_action"] = "run sweep results analysis"
        payload = {
            "schema_version": 1,
            "recommended_next_machine_state": "ANALYZE",
            "launch_requests": [
                {
                    "slot_class": "auxiliary",
                    "mode": "analysis",
                    "worker_ref": "metaopt-analysis-worker",
                    "model_class": "strong_reasoner",
                    "task_file": task_file,
                    "result_file": result_file,
                },
            ],
            "warnings": [],
            "summary": "analysis worker launch request emitted",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_ANALYZE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    analysis = read_json(analysis_result_path)
    improved = analysis.get("improved", False)
    if improved:
        state["baseline"] = {
            "metric": state["objective_snapshot"]["metric"],
            "value": analysis.get("best_metric_value", (state.get("baseline") or {}).get("value")),
            "wandb_run_id": analysis.get("best_run_id", ""),
            "wandb_run_url": analysis.get("best_run_url", ""),
            "established_at": timestamp(),
        }
        state["no_improve_iterations"] = 0
    else:
        state["no_improve_iterations"] = state.get("no_improve_iterations", 0) + 1

    current_sweep = state.get("current_sweep") or {}
    state["completed_iterations"].append({
        "iteration": iteration,
        "sweep_id": current_sweep.get("sweep_id", ""),
        "best_metric_value": analysis.get("best_metric_value"),
        "spend_usd": current_sweep.get("cumulative_spend_usd", 0.0),
        "improved_baseline": improved,
    })

    for learning in analysis.get("learnings", []):
        if learning not in state["key_learnings"]:
            state["key_learnings"].append(learning)

    state["next_action"] = "roll iteration"
    payload = {
        "schema_version": 1,
        "recommended_next_machine_state": "ROLL_ITERATION",
        "warnings": [],
        "summary": "sweep analysis complete; advancing to iteration rollover",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_ANALYZE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    if args.apply_state:
        os.environ["METAOPT_APPLY_STATE_HANDOFF"] = "1"
    load_handoff, _, error = _load_inputs(Path(args.load_handoff), Path(args.state_path))
    handoff_type_map = {
        "gate_local_sanity": _GATE_SANITY_HANDOFF_TYPE,
        "plan_launch": _PLAN_LAUNCH_HANDOFF_TYPE,
        "poll_sweep": _POLL_SWEEP_HANDOFF_TYPE,
        "analyze": _ANALYZE_HANDOFF_TYPE,
    }
    if error is not None:
        payload = _runtime_error(
            Path(args.output),
            handoff_type_map[args.mode],
            error["action"],
            error["summary"],
            error["warnings"],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    state_path = Path(args.state_path)
    if args.mode == "gate_local_sanity":
        payload = _gate_local_sanity(load_handoff, state_path, Path(args.executor_events_dir), Path(args.output))
    elif args.mode == "plan_launch":
        payload = _plan_launch(load_handoff, state_path, Path(args.output))
    elif args.mode == "poll_sweep":
        payload = _poll_sweep(load_handoff, state_path, Path(args.executor_events_dir), Path(args.output))
    else:
        payload = _analyze(load_handoff, state_path, Path(args.tasks_dir), Path(args.worker_results_dir), Path(args.output))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

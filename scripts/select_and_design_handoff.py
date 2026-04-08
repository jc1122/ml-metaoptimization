from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _handoff_utils import emit_handoff, read_json, write_json
from _guardrail_utils import check_lane_drift

_HANDOFF_TYPE = "SELECT_DESIGN"
_CONTROL_AGENT = "metaopt-select-design"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Step 5/6 selection-and-design control handoffs.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "plan_select_experiment",
            "gate_select_and_plan_design",
            "finalize_select_design",
        ),
    )
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--worker-results-dir", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _read_json(path: Path) -> Any:
    return read_json(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def _runtime_error(
    output_path: Path,
    phase: str | None,
    action: str,
    summary: str,
    warnings: list[str] | None = None,
    *,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": phase,
        "outcome": "runtime_error",
        "proposal_id": proposal_id,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "result_file": None,
        "recommended_executor_phase": None,
        "recommended_next_machine_state": None,
        "recommended_next_action": action,
        "selection_rationale": None,
        "design_summary": None,
        "warnings": warnings or [],
        "summary": summary,
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _load_inputs(load_handoff_path: Path, state_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    try:
        load_handoff = _read_json(load_handoff_path)
    except Exception as exc:
        return None, None, {
            "action": "repair or replace load_campaign.latest.json",
            "summary": "load handoff unreadable",
            "warnings": [str(exc)],
        }
    if not isinstance(load_handoff, dict) or load_handoff.get("outcome") != "ok":
        return None, None, {
            "action": "repair or replace load_campaign.latest.json",
            "summary": "load handoff invalid",
            "warnings": [],
        }

    try:
        state = _read_json(state_path)
    except Exception as exc:
        return load_handoff, None, {
            "action": "repair or replace .ml-metaopt/state.json",
            "summary": "state unreadable",
            "warnings": [str(exc)],
        }
    if not isinstance(state, dict):
        return load_handoff, None, {
            "action": "repair or replace .ml-metaopt/state.json",
            "summary": "state invalid",
            "warnings": [],
        }
    return load_handoff, state, None


def _selection_task_paths(iteration: int) -> tuple[str, str]:
    return (
        str(Path(".ml-metaopt") / "tasks" / f"select-experiment-iter-{iteration}.md"),
        str(Path(".ml-metaopt") / "worker-results" / f"select-experiment-iter-{iteration}.json"),
    )


def _design_task_paths(iteration: int) -> tuple[str, str]:
    return (
        str(Path(".ml-metaopt") / "tasks" / f"design-experiment-iter-{iteration}.md"),
        str(Path(".ml-metaopt") / "worker-results" / f"design-experiment-iter-{iteration}.json"),
    )


def _selection_task_markdown(load_handoff: dict[str, Any], state: dict[str, Any], result_file: str) -> str:
    objective = state["objective_snapshot"]
    baseline = state["baseline"]
    return "\n".join(
        [
            f"# Selection Task: iteration-{state['current_iteration']}",
            "",
            "- Worker Kind: `custom_agent`",
            "- Worker Ref: `metaopt-selection-worker`",
            "- Model Class: `strong_reasoner`",
            f"- Result File: `{result_file}`",
            "",
            "## Campaign Context",
            f"- Goal: `{load_handoff.get('goal', '')}`",
            f"- Metric: `{objective.get('metric', '')}`",
            f"- Direction: `{objective.get('direction', '')}`",
            f"- Aggregation: `{json.dumps(objective.get('aggregation', {}), sort_keys=True)}`",
            f"- Improvement Threshold: `{objective.get('improvement_threshold')}`",
            "",
            "## Baseline Context",
            f"- Aggregate Baseline: `{baseline.get('aggregate')}`",
            f"- Per-Dataset Baselines: `{json.dumps(baseline.get('by_dataset', {}), sort_keys=True)}`",
            "",
            "## Selection Inputs",
            f"- Frozen Current Proposals: `{json.dumps(state.get('current_proposals', []), sort_keys=True)}`",
            f"- Proposal Policy: `{json.dumps(load_handoff.get('proposal_policy', {}), sort_keys=True)}`",
            f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`",
            f"- Completed Experiments: `{json.dumps(state.get('completed_experiments', []), sort_keys=True)}`",
            "",
            "Execute only this assigned scope. Do not make control-plane decisions.",
            "Do not launch subagents, generate new proposals, or mutate `.ml-metaopt/state.json`.",
            "Choose exactly one winner from the frozen proposal pool and write one structured JSON result file.",
            "",
            "Expected JSON fields:",
            "- `winning_proposal`",
            "- `ranking_rationale`",
            "- optional `ranked_candidates`",
        ]
    ) + "\n"


def _design_task_markdown(load_handoff: dict[str, Any], state: dict[str, Any], result_file: str) -> str:
    objective = state["objective_snapshot"]
    baseline = state["baseline"]
    winning_proposal = state["selected_experiment"]["proposal_snapshot"]
    return "\n".join(
        [
            f"# Design Task: iteration-{state['current_iteration']}",
            "",
            "- Worker Kind: `custom_agent`",
            "- Worker Ref: `metaopt-design-worker`",
            "- Model Class: `strong_reasoner`",
            f"- Result File: `{result_file}`",
            "",
            "## Campaign Context",
            f"- Goal: `{load_handoff.get('goal', '')}`",
            f"- Metric: `{objective.get('metric', '')}`",
            f"- Direction: `{objective.get('direction', '')}`",
            f"- Aggregation: `{json.dumps(objective.get('aggregation', {}), sort_keys=True)}`",
            f"- Improvement Threshold: `{objective.get('improvement_threshold')}`",
            "",
            "## Winning Proposal",
            f"- Proposal: `{json.dumps(winning_proposal, sort_keys=True)}`",
            f"- Selection Rationale: `{state['selected_experiment'].get('selection_rationale', '')}`",
            "",
            "## Execution Inputs",
            f"- Datasets: `{json.dumps(load_handoff.get('datasets', []), sort_keys=True)}`",
            f"- Execution: `{json.dumps(load_handoff.get('execution', {}), sort_keys=True)}`",
            f"- Remote Queue: `{json.dumps(load_handoff.get('remote_queue', {}), sort_keys=True)}`",
            f"- Baseline: `{json.dumps(baseline, sort_keys=True)}`",
            f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`",
            f"- Completed Experiments: `{json.dumps(state.get('completed_experiments', []), sort_keys=True)}`",
            "",
            "Execute only this assigned scope. Do not make control-plane decisions.",
            "Do not launch subagents, generate code, or mutate `.ml-metaopt/state.json`.",
            "Write one structured JSON experiment design result file.",
            "",
            "Expected JSON fields:",
            "- `proposal_id`",
            "- `experiment_name`",
            "- `description`",
            "- `code_changes`",
            "- `search_space`",
            "- `dataset_plan`",
            "- `artifact_expectations`",
            "- `success_criteria`",
            "- `execution_assumptions`",
            "- `risks`",
        ]
    ) + "\n"


def _validate_winning_proposal(state: dict[str, Any], selection_result: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    winning_proposal = selection_result.get("winning_proposal")
    if not isinstance(winning_proposal, dict):
        return None, "selection result missing winning_proposal"
    proposal_id = winning_proposal.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        return None, "winning proposal missing proposal_id"
    for proposal in state.get("current_proposals", []):
        if proposal.get("proposal_id") == proposal_id:
            return proposal, None
    return None, "winning proposal does not match frozen current_proposals"


def _plan_select_experiment(
    load_handoff: dict[str, Any],
    state_path: Path,
    tasks_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = _read_json(state_path)
    if state.get("selected_experiment") is not None:
        return _runtime_error(
            output_path,
            "PLAN_SELECT_EXPERIMENT",
            "clear stale selected_experiment before re-running selection",
            "selected_experiment already populated",
        )
    if not state.get("current_proposals"):
        return _runtime_error(
            output_path,
            "PLAN_SELECT_EXPERIMENT",
            "rebuild proposal pool before selection",
            "current_proposals is empty",
        )

    task_file, result_file = _selection_task_paths(state["current_iteration"])
    task_path = tasks_dir / Path(task_file).name
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(_selection_task_markdown(load_handoff, state, result_file), encoding="utf-8")

    state["proposal_cycle"]["current_pool_frozen"] = True
    state["machine_state"] = "SELECT_EXPERIMENT"
    state["next_action"] = "run selection worker"
    _write_json(state_path, state)

    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": "PLAN_SELECT_EXPERIMENT",
        "outcome": "planned",
        "proposal_id": None,
        "worker_kind": "custom_agent",
        "worker_ref": "metaopt-selection-worker",
        "task_file": task_file,
        "result_file": result_file,
        "recommended_executor_phase": "RUN_SELECTION",
        "recommended_next_machine_state": "SELECT_EXPERIMENT",
        "recommended_next_action": "launch selection worker",
        "selection_rationale": None,
        "design_summary": None,
        "launch_requests": [
            {
                "slot_class": "auxiliary",
                "mode": "selection",
                "worker_kind": "custom_agent",
                "worker_ref": "metaopt-selection-worker",
                "model_class": "strong_reasoner",
                "task_file": task_file,
                "result_file": result_file,
            },
        ],
        "warnings": [],
        "summary": "selection worker is ready to choose one proposal from the frozen pool",
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _gate_select_and_plan_design(
    load_handoff: dict[str, Any],
    state_path: Path,
    tasks_dir: Path,
    worker_results_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = _read_json(state_path)
    if state.get("selected_experiment") is not None:
        return _runtime_error(
            output_path,
            "GATE_SELECT_AND_PLAN_DESIGN",
            "clear stale selected_experiment before re-running selection",
            "selected_experiment already populated",
        )

    _, selection_result_file = _selection_task_paths(state["current_iteration"])
    selection_result_path = worker_results_dir / Path(selection_result_file).name
    if not selection_result_path.exists():
        return _runtime_error(
            output_path,
            "GATE_SELECT_AND_PLAN_DESIGN",
            "stage selection worker result before gating",
            "selection result missing",
        )

    selection_result = _read_json(selection_result_path)
    if not isinstance(selection_result, dict):
        return _runtime_error(
            output_path,
            "GATE_SELECT_AND_PLAN_DESIGN",
            "repair selection worker result and re-run gating",
            "selection result invalid",
        )

    winning_proposal, error = _validate_winning_proposal(state, selection_result)
    if error:
        return _runtime_error(
            output_path,
            "GATE_SELECT_AND_PLAN_DESIGN",
            "repair selection worker result and re-run gating",
            error,
        )

    selection_rationale = selection_result.get("ranking_rationale")
    if not isinstance(selection_rationale, str) or not selection_rationale:
        return _runtime_error(
            output_path,
            "GATE_SELECT_AND_PLAN_DESIGN",
            "repair selection worker result and re-run gating",
            "selection result missing ranking_rationale",
            proposal_id=winning_proposal["proposal_id"],
        )

    state["selected_experiment"] = {
        "proposal_id": winning_proposal["proposal_id"],
        "proposal_snapshot": winning_proposal,
        "selection_rationale": selection_rationale,
        "sanity_attempts": 0,
        "design": None,
        "diagnosis_history": [],
        "analysis_summary": None,
    }
    state["proposal_cycle"]["current_pool_frozen"] = True
    state["machine_state"] = "DESIGN_EXPERIMENT"
    state["next_action"] = "run design worker"

    task_file, result_file = _design_task_paths(state["current_iteration"])
    task_path = tasks_dir / Path(task_file).name
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(_design_task_markdown(load_handoff, state, result_file), encoding="utf-8")
    _write_json(state_path, state)

    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": "GATE_SELECT_AND_PLAN_DESIGN",
        "outcome": "selection_complete",
        "proposal_id": winning_proposal["proposal_id"],
        "worker_kind": "custom_agent",
        "worker_ref": "metaopt-design-worker",
        "task_file": task_file,
        "result_file": result_file,
        "recommended_executor_phase": "RUN_DESIGN",
        "recommended_next_machine_state": "DESIGN_EXPERIMENT",
        "recommended_next_action": "launch design worker",
        "selection_rationale": selection_rationale,
        "design_summary": None,
        "launch_requests": [
            {
                "slot_class": "auxiliary",
                "mode": "design",
                "worker_kind": "custom_agent",
                "worker_ref": "metaopt-design-worker",
                "model_class": "strong_reasoner",
                "task_file": task_file,
                "result_file": result_file,
            },
        ],
        "warnings": [],
        "summary": "selection result validated and design worker is ready",
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _finalize_select_design(state_path: Path, worker_results_dir: Path, output_path: Path) -> dict[str, Any]:
    state = _read_json(state_path)
    selected_experiment = state.get("selected_experiment")
    if not isinstance(selected_experiment, dict):
        return _runtime_error(
            output_path,
            "FINALIZE_SELECT_DESIGN",
            "persist selected_experiment before finalizing design",
            "selected_experiment missing",
        )
    if selected_experiment.get("design") is not None:
        return _runtime_error(
            output_path,
            "FINALIZE_SELECT_DESIGN",
            "clear stale design before re-running finalization",
            "selected_experiment.design already populated",
            proposal_id=selected_experiment.get("proposal_id"),
        )

    _, design_result_file = _design_task_paths(state["current_iteration"])
    design_result_path = worker_results_dir / Path(design_result_file).name
    if not design_result_path.exists():
        return _runtime_error(
            output_path,
            "FINALIZE_SELECT_DESIGN",
            "stage design worker result before finalizing",
            "design result missing",
            proposal_id=selected_experiment.get("proposal_id"),
        )

    design_result = _read_json(design_result_path)
    if not isinstance(design_result, dict):
        return _runtime_error(
            output_path,
            "FINALIZE_SELECT_DESIGN",
            "repair design worker result and re-run finalization",
            "design result invalid",
            proposal_id=selected_experiment.get("proposal_id"),
        )

    proposal_id = selected_experiment.get("proposal_id")
    if design_result.get("proposal_id") != proposal_id:
        return _runtime_error(
            output_path,
            "FINALIZE_SELECT_DESIGN",
            "repair design worker result and re-run finalization",
            "design result proposal_id does not match selected_experiment",
            proposal_id=proposal_id,
        )

    drift_fields = check_lane_drift("design", design_result)
    if drift_fields:
        return _runtime_error(
            output_path,
            "FINALIZE_SELECT_DESIGN",
            "remove materialization fields from design result and re-run finalization",
            f"design result contains materialization-lane fields: {drift_fields}",
            proposal_id=proposal_id,
        )

    selected_experiment["design"] = design_result
    state["proposal_cycle"]["current_pool_frozen"] = True
    state["machine_state"] = "MATERIALIZE_CHANGESET"
    state["next_action"] = "materialize selected experiment"
    _write_json(state_path, state)

    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": "FINALIZE_SELECT_DESIGN",
        "outcome": "selected_and_designed",
        "proposal_id": proposal_id,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "result_file": design_result_file,
        "recommended_executor_phase": None,
        "recommended_next_machine_state": "MATERIALIZE_CHANGESET",
        "recommended_next_action": "materialize selected experiment",
        "selection_rationale": selected_experiment.get("selection_rationale"),
        "design_summary": design_result.get("description") or design_result.get("experiment_name"),
        "warnings": [],
        "summary": "experiment design finalized and ready for materialization",
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    load_handoff_path = Path(args.load_handoff)
    state_path = Path(args.state_path)
    tasks_dir = Path(args.tasks_dir)
    worker_results_dir = Path(args.worker_results_dir)
    output_path = Path(args.output)

    load_handoff, state, error = _load_inputs(load_handoff_path, state_path)
    if error is not None:
        payload = _runtime_error(
            output_path,
            None,
            error["action"],
            error["summary"],
            error["warnings"],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.mode == "plan_select_experiment":
        payload = _plan_select_experiment(load_handoff, state_path, tasks_dir, output_path)
    elif args.mode == "gate_select_and_plan_design":
        payload = _gate_select_and_plan_design(load_handoff, state_path, tasks_dir, worker_results_dir, output_path)
    else:
        payload = _finalize_select_design(state_path, worker_results_dir, output_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

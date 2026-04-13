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
    write_json,
)

_CONTROL_AGENT = "metaopt-select-design"
_PLAN_HANDOFF_TYPE = "select_design.plan_select_design"
_FINALIZE_HANDOFF_TYPE = "select_design.finalize_select_design"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Step 5/6 selection-and-design control handoffs.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "plan_select_design",
            "finalize_select_design",
        ),
    )
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
    *,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "proposal_id": proposal_id,
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
        return None, None, {
            "action": "repair or replace load_campaign.latest.json",
            "summary": "load handoff unreadable",
            "warnings": [str(exc)],
        }
    if not load_campaign_handoff_is_ready(load_handoff):
        return None, None, {
            "action": "repair or replace load_campaign.latest.json",
            "summary": "load handoff invalid",
            "warnings": [],
        }

    try:
        state = read_json(state_path)
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
        str(Path(".ml-metaopt") / "tasks" / f"select-design-iter-{iteration}.md"),
        str(Path(".ml-metaopt") / "worker-results" / f"select-design-iter-{iteration}.json"),
    )


def _selection_task_markdown(load_handoff: dict[str, Any], state: dict[str, Any], result_file: str) -> str:
    objective = state["objective_snapshot"]
    baseline = state.get("baseline") or {}
    return "\n".join(
        [
            f"# Select & Design Task: iteration-{state['current_iteration']}",
            "",
            "- Worker Kind: `custom_agent`",
            "- Worker Ref: `metaopt-selection-worker`",
            "- Model Class: `strong_reasoner`",
            f"- Result File: `{result_file}`",
            "",
            "## Campaign Context",
            f"- Metric: `{objective.get('metric', '')}`",
            f"- Direction: `{objective.get('direction', '')}`",
            f"- Improvement Threshold: `{objective.get('improvement_threshold')}`",
            "",
            "## Baseline Context",
            f"- Baseline: `{json.dumps(baseline, sort_keys=True)}`",
            "",
            "## Selection Inputs",
            f"- Frozen Current Proposals: `{json.dumps(state.get('current_proposals', []), sort_keys=True)}`",
            f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`",
            "",
            "Execute only this assigned scope. Do not make control-plane decisions.",
            "Choose exactly one winner from the frozen proposal pool.",
            "Produce a WandB sweep_config for the selected proposal.",
            "",
            "Expected JSON fields:",
            "- `winning_proposal`",
            "- `sweep_config`",
            "- `ranking_rationale`",
        ]
    ) + "\n"


def _plan_select_design(
    load_handoff: dict[str, Any],
    state_path: Path,
    tasks_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    if state.get("selected_sweep") is not None:
        return _runtime_error(
            output_path,
            _PLAN_HANDOFF_TYPE,
            "clear stale selected_sweep before re-running selection",
            "selected_sweep already populated",
        )
    if not state.get("current_proposals"):
        return _runtime_error(
            output_path,
            _PLAN_HANDOFF_TYPE,
            "rebuild proposal pool before selection",
            "current_proposals is empty",
        )

    task_file, result_file = _selection_task_paths(state["current_iteration"])
    task_path = tasks_dir / Path(task_file).name
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(_selection_task_markdown(load_handoff, state, result_file), encoding="utf-8")

    state["proposal_cycle"]["current_pool_frozen"] = True
    state["next_action"] = "run selection worker"

    payload = {
        "schema_version": 1,
        "proposal_id": None,
        "recommended_next_machine_state": "SELECT_AND_DESIGN_SWEEP",
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
        "summary": "selection worker is ready to choose one proposal from the frozen pool",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


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


def _finalize_select_design(
    load_handoff: dict[str, Any],
    state_path: Path,
    worker_results_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = read_json(state_path)
    previous_state = deepcopy(state)
    if state.get("selected_sweep") is not None:
        return _runtime_error(
            output_path,
            _FINALIZE_HANDOFF_TYPE,
            "clear stale selected_sweep before re-running",
            "selected_sweep already populated",
        )

    _, selection_result_file = _selection_task_paths(state["current_iteration"])
    selection_result_path = worker_results_dir / Path(selection_result_file).name
    if not selection_result_path.exists():
        return _runtime_error(
            output_path,
            _FINALIZE_HANDOFF_TYPE,
            "stage selection worker result before finalizing",
            "selection result missing",
        )

    selection_result = read_json(selection_result_path)
    if not isinstance(selection_result, dict):
        return _runtime_error(
            output_path,
            _FINALIZE_HANDOFF_TYPE,
            "repair selection worker result and re-run",
            "selection result invalid",
        )

    winning_proposal, error = _validate_winning_proposal(state, selection_result)
    if error:
        return _runtime_error(
            output_path,
            _FINALIZE_HANDOFF_TYPE,
            "repair selection worker result and re-run",
            error,
        )

    sweep_config = selection_result.get("sweep_config")
    if not isinstance(sweep_config, dict) or not sweep_config:
        return _runtime_error(
            output_path,
            _FINALIZE_HANDOFF_TYPE,
            "repair selection worker result: sweep_config missing",
            "selection result missing sweep_config",
            proposal_id=winning_proposal["proposal_id"],
        )

    state["selected_sweep"] = {
        "proposal_id": winning_proposal["proposal_id"],
        "sweep_config": sweep_config,
    }
    state["proposal_cycle"]["current_pool_frozen"] = True
    state["next_action"] = "run local sanity check"

    payload = {
        "schema_version": 1,
        "proposal_id": winning_proposal["proposal_id"],
        "recommended_next_machine_state": "LOCAL_SANITY",
        "warnings": [],
        "summary": "sweep design finalized and ready for local sanity",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_FINALIZE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    if args.apply_state:
        os.environ["METAOPT_APPLY_STATE_HANDOFF"] = "1"
    load_handoff_path = Path(args.load_handoff)
    state_path = Path(args.state_path)
    tasks_dir = Path(args.tasks_dir)
    worker_results_dir = Path(args.worker_results_dir)
    output_path = Path(args.output)

    load_handoff, state, error = _load_inputs(load_handoff_path, state_path)
    if error is not None:
        payload = _runtime_error(
            output_path,
            {
                "plan_select_design": _PLAN_HANDOFF_TYPE,
                "finalize_select_design": _FINALIZE_HANDOFF_TYPE,
            }[args.mode],
            error["action"],
            error["summary"],
            error["warnings"],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.mode == "plan_select_design":
        payload = _plan_select_design(load_handoff, state_path, tasks_dir, output_path)
    else:
        payload = _finalize_select_design(load_handoff, state_path, worker_results_dir, output_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

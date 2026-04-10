from __future__ import annotations

import argparse
from copy import deepcopy
import json
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

_CONTROL_AGENT = "metaopt-local-execution-control"
_PLAN_HANDOFF_TYPE = "local_execution.plan_local_changeset"
_GATE_HANDOFF_TYPE = "local_execution.gate_local_sanity"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Steps 7/8 local execution control handoffs.")
    parser.add_argument("--mode", required=True, choices=("plan_local_changeset", "gate_local_sanity"))
    parser.add_argument("--load-handoff", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--worker-results-dir", required=True)
    parser.add_argument("--executor-events-dir", required=True)
    parser.add_argument("--output", required=True)
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
        "materialization_mode": None,
        "task_file": None,
        "result_file": None,
        "required_worktree": None,
        "sanity_attempts": None,
        "recommended_next_machine_state": None,
        "recovery_action": recovery_action,
        "diagnosis_action": None,
        "state_patch": None,
        "warnings": warnings or [],
        "summary": summary,
    }
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


def _attempt_number(state: dict[str, Any]) -> int:
    selected = state["selected_experiment"]
    return int(selected.get("sanity_attempts", 0)) + 1


def _latest_local_changeset_event(executor_events_dir: Path, attempt: int) -> dict[str, Any] | None:
    path = executor_events_dir / f"local_changeset-{attempt}.json"
    if not path.exists():
        return None
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else None


def _has_apply_conflict(local_changeset_event: dict[str, Any] | None) -> bool:
    if not isinstance(local_changeset_event, dict):
        return False
    for result in local_changeset_event.get("apply_results", []):
        if not isinstance(result, dict):
            continue
        status = result.get("status")
        error = (result.get("error") or "").lower()
        if status in {"conflict", "failed"}:
            return True
        if "conflict" in error or "merge" in error:
            return True
    return False


def _task_markdown(
    state: dict[str, Any],
    load_handoff: dict[str, Any],
    materialization_mode: str,
    attempt: int,
    required_worktree: str,
    local_changeset_event: dict[str, Any] | None,
) -> str:
    design = state["selected_experiment"]["design"]
    diagnosis_history = state["selected_experiment"].get("diagnosis_history", [])
    latest_diagnosis = diagnosis_history[-1] if diagnosis_history else None
    result_file = f".ml-metaopt/worker-results/materialization-{attempt}.json"
    worker_kind = "custom_agent"
    worker_ref = "metaopt-materialization-worker"
    lines = [
        f"# Local Changeset Task: materialization-{attempt}",
        "",
        f"- Attempt: `{attempt}`",
        f"- Materialization Mode: `{materialization_mode}`",
        f"- Worker Kind: `{worker_kind}`",
        f"- Worker Ref: `{worker_ref}`",
        "- Model Class: `strong_coder`",
        f"- Required Worktree: `{required_worktree}`",
        f"- Result File: `{result_file}`",
        "",
        "## Inputs",
        f"- Experiment Design: `state.selected_experiment.design` ({design.get('proposal_id')})",
        f"- Primary Intervention: `{design.get('primary_intervention', '')}`",
        f"- Execution Config: `{load_handoff.get('execution', {}).get('entrypoint', '')}`",
        f"- Artifact Roots: `{', '.join(load_handoff.get('artifacts', {}).get('code_roots', []))}`",
        f"- Exclude Paths: `{', '.join(load_handoff.get('artifacts', {}).get('exclude', []))}`",
    ]
    if state.get("key_learnings") and materialization_mode == "standard":
        lines.append(f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`")
    if latest_diagnosis is not None and materialization_mode == "remediation":
        lines.append(f"- Diagnosis Guidance: `{latest_diagnosis.get('code_guidance')}`")
        lines.append(f"- Diagnosis History: `{json.dumps(diagnosis_history, sort_keys=True)}`")
        lines.append(f"- Current Local Changeset: `{json.dumps(state.get('local_changeset'), sort_keys=True)}`")
    if materialization_mode == "conflict_resolution":
        apply_results = local_changeset_event.get("apply_results", []) if isinstance(local_changeset_event, dict) else []
        lines.append(f"- Conflicting Apply Results: `{json.dumps(apply_results, sort_keys=True)}`")
        lines.append(f"- Integration Worktree: `{(local_changeset_event or {}).get('integration_worktree', required_worktree)}`")
    lines.extend(
        [
            "",
            "Execute only this assigned scope. Do not make control-plane decisions.",
            "Do not launch subagents, apply patches mechanically, package artifacts, or run sanity commands.",
            "Write one structured JSON result file to the exact result path shown above.",
            "",
            "Expected JSON fields:",
            "- `status`",
            "- `patch_artifacts`",
            "- `verification_notes`",
            "- optional `summary`",
        ]
    )
    return "\n".join(lines) + "\n"


def _diagnosis_task_markdown(state: dict[str, Any], load_handoff: dict[str, Any], attempt: int, sanity_event: dict[str, Any]) -> str:
    design = state["selected_experiment"]["design"]
    local_changeset = state.get("local_changeset")
    diagnosis_history = state["selected_experiment"].get("diagnosis_history", [])
    result_file = f".ml-metaopt/worker-results/diagnosis-{attempt}.json"
    lines = [
        f"# Local Diagnosis Task: diagnosis-{attempt}",
        "",
        f"- Attempt: `{attempt}`",
        "- Worker Kind: `custom_agent`",
        "- Worker Ref: `metaopt-diagnosis-worker`",
        "- Model Class: `strong_reasoner`",
        f"- Result File: `{result_file}`",
        "",
        "## Failure Context",
        "- Failure Type: `local_sanity`",
        f"- Exit Code: `{sanity_event.get('exit_code')}`",
        f"- Stdout: `{sanity_event.get('stdout', '')}`",
        f"- Stderr: `{sanity_event.get('stderr', '')}`",
        f"- Duration Seconds: `{sanity_event.get('duration_seconds')}`",
        "",
        "## Inputs",
        f"- Experiment Design: `state.selected_experiment.design` ({design.get('proposal_id')})",
        f"- Primary Intervention: `{design.get('primary_intervention', '')}`",
        f"- Current Local Changeset: `{json.dumps(local_changeset, sort_keys=True)}`",
        f"- Sanity Config: `{json.dumps(load_handoff.get('sanity', {}), sort_keys=True)}`",
        f"- Previous Diagnoses: `{json.dumps(diagnosis_history, sort_keys=True)}`",
        f"- Attempt Number: `{attempt}`",
        "- Max Attempts: `3`",
        "",
        "Execute only this assigned scope. Do not make control-plane decisions.",
        "Do not launch subagents, patch code, or mutate `.ml-metaopt/state.json`.",
        "Write one structured JSON result file to the exact result path shown above.",
        "",
        "Expected JSON fields:",
        "- `root_cause`",
        "- `classification`",
        "- `fix_recommendation`",
        "- `confidence`",
    ]
    return "\n".join(lines) + "\n"


def _plan_local_changeset(
    load_handoff: dict[str, Any],
    state_path: Path,
    tasks_dir: Path,
    worker_results_dir: Path,
    executor_events_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = _read_json(state_path)
    previous_state = deepcopy(state)
    if state.get("selected_experiment") is None or state["selected_experiment"].get("design") is None:
        return _runtime_error(
            output_path,
            _PLAN_HANDOFF_TYPE,
            "repair selected_experiment design before materialization",
            "selected experiment design missing",
        )

    attempt = _attempt_number(state)
    diagnosis_history = state["selected_experiment"].get("diagnosis_history", [])
    latest_diagnosis = diagnosis_history[-1] if diagnosis_history else None
    local_changeset_event = _latest_local_changeset_event(executor_events_dir, attempt)
    if _has_apply_conflict(local_changeset_event):
        materialization_mode = "conflict_resolution"
    elif latest_diagnosis and latest_diagnosis.get("action") == "fix":
        # Require actual diagnosis artifact before entering remediation
        prev_attempt = attempt - 1
        diagnosis_artifact = worker_results_dir / f"diagnosis-{prev_attempt}.json"
        if not diagnosis_artifact.exists():
            state["status"] = "BLOCKED_PROTOCOL"
            state["machine_state"] = "BLOCKED_PROTOCOL"
            state["next_action"] = (
                "protocol violation: remediation requested but diagnosis artifact "
                f"diagnosis-{prev_attempt}.json is missing; manual intervention required"
            )
            payload = {
                "schema_version": 1,
                "worker_kind": None,
                "worker_ref": None,
                "materialization_mode": None,
                "task_file": None,
                "result_file": None,
                "required_worktree": None,
                "sanity_attempts": state["selected_experiment"].get("sanity_attempts", 0),
                "recommended_next_machine_state": "BLOCKED_PROTOCOL",
                "diagnosis_action": None,
                "warnings": [f"diagnosis artifact diagnosis-{prev_attempt}.json not found"],
                "summary": "remediation blocked: diagnosis artifact missing",
            }
            persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
            return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)
        materialization_mode = "remediation"
    else:
        materialization_mode = "standard"

    required_worktree = f".ml-metaopt/worktrees/iter-{state['current_iteration']}-materialization"
    if materialization_mode == "conflict_resolution" and isinstance(local_changeset_event, dict):
        required_worktree = local_changeset_event.get("integration_worktree") or required_worktree
    task_name = f"materialization-{attempt}.md"
    task_path = tasks_dir / task_name
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        _task_markdown(
            state,
            load_handoff,
            materialization_mode,
            attempt,
            required_worktree,
            local_changeset_event,
        ),
        encoding="utf-8",
    )

    state["machine_state"] = "MATERIALIZE_CHANGESET"
    state["next_action"] = "execute local changeset plan"

    # Standard materialization emits explicit executor directives for the
    # mechanical steps that follow worker completion.  Remediation and
    # conflict-resolution paths only re-launch the worker, so they carry
    # no executor directives.
    if materialization_mode == "standard":
        artifacts = load_handoff.get("artifacts", {})
        sanity_cfg = load_handoff.get("sanity", {})
        executor_directives: list[dict[str, Any]] = [
            {
                "action": "apply_patch_artifacts",
                "reason": "apply materialization patches to integration worktree",
                "worktree": required_worktree,
                "target_worktree": required_worktree,
                "result_file": str(Path(".ml-metaopt") / "worker-results" / f"materialization-{attempt}.json"),
            },
            {
                "action": "package_code_artifact",
                "reason": "package code tree for remote execution",
                "worktree": required_worktree,
                "code_roots": artifacts.get("code_roots", ["."]),
                "exclude": artifacts.get("exclude", []),
            },
            {
                "action": "package_data_manifest",
                "reason": "build data manifest for remote execution",
                "worktree": required_worktree,
                "data_roots": artifacts.get("data_roots", []),
            },
            {
                "action": "run_sanity",
                "reason": "run local sanity check before proceeding to remote enqueue",
                "worktree": required_worktree,
                "command": sanity_cfg.get("command", ""),
                "max_duration_seconds": sanity_cfg.get("max_duration_seconds"),
            },
        ]
    else:
        executor_directives = []

    task_file_path = str(Path(".ml-metaopt") / "tasks" / task_name)
    result_file_path = str(Path(".ml-metaopt") / "worker-results" / f"materialization-{attempt}.json")
    payload = {
        "schema_version": 1,
        "worker_kind": "custom_agent",
        "worker_ref": "metaopt-materialization-worker",
        "materialization_mode": materialization_mode,
        "task_file": task_file_path,
        "result_file": result_file_path,
        "required_worktree": required_worktree,
        "sanity_attempts": state["selected_experiment"].get("sanity_attempts", 0),
        "recommended_next_machine_state": "MATERIALIZE_CHANGESET",
        "diagnosis_action": None,
        "launch_requests": [
            {
                "slot_class": "auxiliary",
                "mode": "materialization",
                "worker_ref": "metaopt-materialization-worker",
                "model_class": "strong_coder",
                "task_file": task_file_path,
                "result_file": result_file_path,
            },
        ],
        "executor_directives": executor_directives,
        "warnings": [],
        "summary": f"planned {materialization_mode} local materialization task",
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_PLAN_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _build_local_changeset(materialization_result: dict[str, Any], local_changeset_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "patch_artifacts": materialization_result.get("patch_artifacts", []),
        "apply_results": local_changeset_event.get("apply_results", []),
        "verification_notes": materialization_result.get("verification_notes", []),
        "code_artifact_uri": local_changeset_event.get("code_artifact_uri"),
        "data_manifest_uri": local_changeset_event.get("data_manifest_uri"),
        "integration_worktree": local_changeset_event.get("integration_worktree"),
    }


def _append_diagnosis(state: dict[str, Any], diagnosis_result: dict[str, Any], attempt: int) -> str:
    recommendation = diagnosis_result.get("fix_recommendation", {})
    action = recommendation.get("action", "abandon")
    state["selected_experiment"]["diagnosis_history"].append(
        {
            "attempt": attempt,
            "root_cause": diagnosis_result.get("root_cause"),
            "classification": diagnosis_result.get("classification"),
            "action": action,
            "code_guidance": recommendation.get("code_guidance"),
            "config_guidance": recommendation.get("config_guidance"),
            "diagnosed_at": _timestamp(),
        }
    )
    state["selected_experiment"]["sanity_attempts"] = attempt
    return action


def _gate_local_sanity(
    load_handoff: dict[str, Any],
    tasks_dir: Path,
    state_path: Path,
    worker_results_dir: Path,
    executor_events_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = _read_json(state_path)
    previous_state = deepcopy(state)
    if state.get("selected_experiment") is None:
        return _runtime_error(
            output_path,
            _GATE_HANDOFF_TYPE,
            "repair selected_experiment before local sanity",
            "selected experiment missing",
        )

    attempt = _attempt_number(state)
    sanity_event_path = executor_events_dir / f"sanity-{attempt}.json"
    if not sanity_event_path.exists():
        return _runtime_error(
            output_path,
            _GATE_HANDOFF_TYPE,
            "run sanity.command and stage raw output",
            "sanity event missing",
        )
    sanity_event = _read_json(sanity_event_path)

    if sanity_event.get("status") == "passed":
        materialization_result_path = worker_results_dir / f"materialization-{attempt}.json"
        local_changeset_event_path = executor_events_dir / f"local_changeset-{attempt}.json"
        if not materialization_result_path.exists() or not local_changeset_event_path.exists():
            return _runtime_error(
                output_path,
                _GATE_HANDOFF_TYPE,
                "stage materialization and local changeset outputs before gating sanity",
                "materialization outputs missing",
            )
        materialization_result = _read_json(materialization_result_path)
        local_changeset_event = _read_json(local_changeset_event_path)
        state["local_changeset"] = _build_local_changeset(materialization_result, local_changeset_event)
        state["machine_state"] = "ENQUEUE_REMOTE_BATCH"
        state["next_action"] = "enqueue remote batch"
        payload = {
            "schema_version": 1,
            "worker_kind": None,
            "worker_ref": None,
            "materialization_mode": None,
            "task_file": None,
            "result_file": None,
            "required_worktree": state["local_changeset"].get("integration_worktree"),
            "sanity_attempts": state["selected_experiment"].get("sanity_attempts", 0),
            "recommended_next_machine_state": "ENQUEUE_REMOTE_BATCH",
            "diagnosis_action": None,
            "warnings": [],
            "summary": "local changeset passed sanity and is ready for remote enqueue",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_GATE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    if state["selected_experiment"].get("sanity_attempts", 0) >= 3:
        state["status"] = "FAILED"
        state["machine_state"] = "FAILED"
        state["next_action"] = "stop after repeated local sanity failures"
        payload = {
            "schema_version": 1,
            "worker_kind": None,
            "worker_ref": None,
            "materialization_mode": None,
            "task_file": None,
            "result_file": str(Path(".ml-metaopt") / "worker-results" / f"diagnosis-{attempt}.json"),
            "required_worktree": None,
            "sanity_attempts": state["selected_experiment"]["sanity_attempts"],
            "recommended_next_machine_state": "FAILED",
            "diagnosis_action": None,
            "warnings": [],
            "summary": "sanity remediation attempt cap reached",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_GATE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    diagnosis_result_path = worker_results_dir / f"diagnosis-{attempt}.json"
    if not diagnosis_result_path.exists():
        task_path = tasks_dir / f"diagnosis-{attempt}.md"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(_diagnosis_task_markdown(state, load_handoff, attempt, sanity_event), encoding="utf-8")
        state["machine_state"] = "LOCAL_SANITY"
        state["next_action"] = "run sanity diagnosis"
        payload = {
            "schema_version": 1,
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-diagnosis-worker",
            "materialization_mode": None,
            "task_file": str(Path(".ml-metaopt") / "tasks" / f"diagnosis-{attempt}.md"),
            "result_file": str(Path(".ml-metaopt") / "worker-results" / f"diagnosis-{attempt}.json"),
            "required_worktree": None,
            "sanity_attempts": state["selected_experiment"].get("sanity_attempts", 0),
            "recommended_next_machine_state": "LOCAL_SANITY",
            "diagnosis_action": None,
            "launch_requests": [
                {
                    "slot_class": "auxiliary",
                    "mode": "diagnosis",
                    "worker_kind": "custom_agent",
                    "worker_ref": "metaopt-diagnosis-worker",
                    "model_class": "strong_reasoner",
                    "task_file": str(Path(".ml-metaopt") / "tasks" / f"diagnosis-{attempt}.md"),
                    "result_file": str(Path(".ml-metaopt") / "worker-results" / f"diagnosis-{attempt}.json"),
                },
            ],
            "warnings": [],
            "summary": "sanity failed and requires diagnosis before routing",
        }
        persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
        return emit_handoff(output_path, payload, handoff_type=_GATE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    diagnosis_result = _read_json(diagnosis_result_path)
    action = _append_diagnosis(state, diagnosis_result, attempt)
    recommendation = diagnosis_result.get("fix_recommendation", {})

    if action == "fix":
        state["machine_state"] = "MATERIALIZE_CHANGESET"
        state["next_action"] = "materialize remediation changeset"
        next_state = "MATERIALIZE_CHANGESET"
        summary = "diagnosis requested a remediation materialization pass"
    elif action == "adjust_config":
        state["status"] = "BLOCKED_CONFIG"
        state["machine_state"] = "BLOCKED_CONFIG"
        state["next_action"] = recommendation.get("config_guidance") or "repair campaign configuration"
        next_state = "BLOCKED_CONFIG"
        summary = "diagnosis identified a configuration issue that blocks local execution"
    else:
        state["status"] = "FAILED"
        state["machine_state"] = "FAILED"
        state["next_action"] = diagnosis_result.get("root_cause") or "stop after local execution failure"
        next_state = "FAILED"
        summary = "diagnosis marked the selected experiment as unrecoverable"

    payload = {
        "schema_version": 1,
        "worker_kind": None,
        "worker_ref": None,
        "materialization_mode": None,
        "task_file": None,
        "result_file": str(Path(".ml-metaopt") / "worker-results" / f"diagnosis-{attempt}.json"),
        "required_worktree": None,
        "sanity_attempts": state["selected_experiment"]["sanity_attempts"],
        "recommended_next_machine_state": next_state,
        "diagnosis_action": action,
        "warnings": [],
        "summary": summary,
    }
    persist_state_handoff(state_path, previous_state, state, payload, control_agent=_CONTROL_AGENT)
    return emit_handoff(output_path, payload, handoff_type=_GATE_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    load_handoff_path = Path(args.load_handoff)
    state_path = Path(args.state_path)
    tasks_dir = Path(args.tasks_dir)
    worker_results_dir = Path(args.worker_results_dir)
    executor_events_dir = Path(args.executor_events_dir)
    output_path = Path(args.output)

    load_handoff, state, error = _load_inputs(load_handoff_path, state_path)
    if error is not None:
        payload = _runtime_error(
            output_path,
            _PLAN_HANDOFF_TYPE if args.mode == "plan_local_changeset" else _GATE_HANDOFF_TYPE,
            error["action"],
            error["summary"],
            error["warnings"],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.mode == "plan_local_changeset":
        payload = _plan_local_changeset(load_handoff, state_path, tasks_dir, worker_results_dir, executor_events_dir, output_path)
    else:
        payload = _gate_local_sanity(load_handoff, tasks_dir, state_path, worker_results_dir, executor_events_dir, output_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

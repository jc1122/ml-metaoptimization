from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _handoff_utils import emit_handoff, read_json, timestamp, write_json

_HANDOFF_TYPE = "REMOTE_EXECUTION_CONTROL"
_CONTROL_AGENT = "metaopt-remote-execution-control"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Steps 9/11 remote execution control handoffs.")
    parser.add_argument("--mode", required=True, choices=("plan_remote_batch", "gate_remote_batch", "analyze_remote_results"))
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
    phase: str | None,
    action: str,
    summary: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": phase,
        "outcome": "runtime_error",
        "batch_id": None,
        "manifest_path": None,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "recommended_executor_phase": None,
        "recommended_next_machine_state": None,
        "recommended_next_action": action,
        "judgment": None,
        "delta": None,
        "warnings": warnings or [],
        "summary": summary,
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _load_inputs(load_handoff_path: Path, state_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    try:
        load_handoff = _read_json(load_handoff_path)
    except Exception as exc:
        return None, None, {"action": "repair or replace load_campaign.latest.json", "summary": "load handoff unreadable", "warnings": [str(exc)]}
    if not isinstance(load_handoff, dict) or load_handoff.get("outcome") != "ok":
        return None, None, {"action": "repair or replace load_campaign.latest.json", "summary": "load handoff invalid", "warnings": []}

    try:
        state = _read_json(state_path)
    except Exception as exc:
        return load_handoff, None, {"action": "repair or replace .ml-metaopt/state.json", "summary": "state unreadable", "warnings": [str(exc)]}
    if not isinstance(state, dict):
        return load_handoff, None, {"action": "repair or replace .ml-metaopt/state.json", "summary": "state invalid", "warnings": []}
    return load_handoff, state, None


def _extract_batch_sequence(batch_id: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"batch-(\d{8})-(\d{4})", batch_id)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _next_batch_id(state: dict[str, Any]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    max_seen = 0
    for collection_name in ("remote_batches", "completed_experiments"):
        for record in state.get(collection_name, []):
            batch_id = record.get("batch_id")
            if isinstance(batch_id, str):
                parsed = _extract_batch_sequence(batch_id)
                if parsed:
                    max_seen = max(max_seen, parsed[1])
    return f"batch-{today}-{max_seen + 1:04d}"


def _pending_batch(state: dict[str, Any]) -> dict[str, Any] | None:
    pending = state.get("pending_remote_batch")
    if not isinstance(pending, dict):
        return None
    batch_id = pending.get("batch_id")
    manifest_path = pending.get("manifest_path")
    if not isinstance(batch_id, str) or not batch_id:
        return None
    if not isinstance(manifest_path, str) or not manifest_path:
        return None
    return pending


def _active_batch_id(state: dict[str, Any]) -> str | None:
    remote_batches = state.get("remote_batches", [])
    if not remote_batches:
        return None
    return remote_batches[-1].get("batch_id")


def _analysis_task_markdown(batch_id: str, state: dict[str, Any]) -> str:
    proposal_id = state["selected_experiment"]["proposal_id"]
    objective = state["objective_snapshot"]
    baseline = state["baseline"]
    design = state["selected_experiment"].get("design", {})
    local_changeset = state.get("local_changeset", {})
    remote_batch = next((batch for batch in state.get("remote_batches", []) if batch.get("batch_id") == batch_id), {})
    return "\n".join(
        [
            f"# Remote Analysis Task: {batch_id}",
            "",
            f"- Batch ID: `{batch_id}`",
            "- Worker Kind: `custom_agent`",
            "- Worker Ref: `metaopt-analysis-worker`",
            "- Model Class: `strong_reasoner`",
            f"- Proposal ID: `{proposal_id}`",
            f"- Result File: `.ml-metaopt/worker-results/remote-analysis-{batch_id}.json`",
            "",
            "## Objective Context",
            f"- Metric: `{objective.get('metric', '')}`",
            f"- Direction: `{objective.get('direction', '')}`",
            f"- Aggregation Method: `{objective.get('aggregation', {}).get('method', '')}`",
            f"- Aggregation Weights: `{json.dumps(objective.get('aggregation', {}).get('weights'), sort_keys=True)}`",
            f"- Improvement Threshold: `{objective.get('improvement_threshold')}`",
            "",
            "## Baseline Context",
            f"- Aggregate Baseline: `{baseline.get('aggregate')}`",
            f"- Per-Dataset Baselines: `{json.dumps(baseline.get('by_dataset', {}), sort_keys=True)}`",
            "",
            "## Experiment Context",
            f"- Selected Experiment Design: `{json.dumps(design, sort_keys=True)}`",
            f"- Local Changeset Summary: `{json.dumps(local_changeset, sort_keys=True)}`",
            f"- Key Learnings: `{json.dumps(state.get('key_learnings', []), sort_keys=True)}`",
            f"- Completed Experiments: `{json.dumps(state.get('completed_experiments', []), sort_keys=True)}`",
            "",
            "## Result Context",
            f"- Remote Batch Record: `{json.dumps(remote_batch, sort_keys=True)}`",
            "Execute only this assigned scope. Do not make control-plane decisions.",
            "Do not launch subagents, call backend commands, or mutate `.ml-metaopt/state.json`.",
            "Use the staged backend results plus current baseline to produce structured analysis JSON.",
            "",
            "Expected JSON fields:",
            "- `judgment`",
            "- `new_aggregate`",
            "- `delta`",
            "- `learnings`",
            "- `invalidations`",
            "- `carry_over_candidates`",
        ]
    ) + "\n"


def _diagnosis_task_markdown(batch_id: str, failure_context: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Remote Diagnosis Task: {batch_id}",
            "",
            f"- Batch ID: `{batch_id}`",
            "- Worker Kind: `custom_agent`",
            "- Worker Ref: `metaopt-diagnosis-worker`",
            "- Model Class: `strong_reasoner`",
            f"- Result File: `.ml-metaopt/worker-results/remote-diagnosis-{batch_id}.json`",
            "",
            "Execute only this assigned scope. Do not make control-plane decisions.",
            "Do not launch subagents, patch code, or mutate `.ml-metaopt/state.json`.",
            f"- Failure Classification: `{failure_context.get('classification', '')}`",
            f"- Failure Message: `{failure_context.get('message', '')}`",
            f"- Return Code: `{failure_context.get('returncode', '')}`",
            "",
            "Expected JSON fields:",
            "- `root_cause`",
            "- `classification`",
            "- `fix_recommendation`",
            "- `confidence`",
            "- optional `learnings`",
        ]
    ) + "\n"


def _improvement_clears_threshold(state: dict[str, Any], analysis_result: dict[str, Any]) -> bool:
    objective = state["objective_snapshot"]
    threshold = objective["improvement_threshold"]
    delta = analysis_result["delta"]
    if objective["direction"] == "minimize":
        return delta <= -threshold
    return delta >= threshold


def _plan_remote_batch(load_handoff: dict[str, Any], state_path: Path, output_path: Path) -> dict[str, Any]:
    state = _read_json(state_path)
    if state.get("local_changeset") is None:
        return _runtime_error(
            output_path,
            "PLAN_REMOTE_BATCH",
            "repair local_changeset before enqueue",
            "local_changeset missing",
        )

    pending_batch = _pending_batch(state)
    if pending_batch is None:
        batch_id = _next_batch_id(state)
        manifest_path = str(Path(".ml-metaopt") / "artifacts" / "manifests" / f"{batch_id}.json")
        state["pending_remote_batch"] = {"batch_id": batch_id, "manifest_path": manifest_path}
        _write_json(state_path, state)
    else:
        batch_id = pending_batch["batch_id"]
        manifest_path = pending_batch["manifest_path"]

    enqueue_command = load_handoff["remote_queue"]["enqueue_command"]
    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": "PLAN_REMOTE_BATCH",
        "outcome": "planned",
        "batch_id": batch_id,
        "manifest_path": manifest_path,
        "task_file": None,
        "enqueue_command": enqueue_command,
        "recommended_executor_phase": "EXECUTE_REMOTE_ENQUEUE",
        "recommended_next_machine_state": "ENQUEUE_REMOTE_BATCH",
        "recommended_next_action": "write manifest and enqueue batch",
        "judgment": None,
        "delta": None,
        "executor_directives": [
            {
                "action": "write_manifest",
                "reason": "batch manifest must be written before enqueue",
                "batch_id": batch_id,
                "manifest_path": manifest_path,
            },
            {
                "action": "enqueue_batch",
                "reason": "submit batch to remote queue backend",
                "command": enqueue_command,
                "manifest_path": manifest_path,
                "batch_id": batch_id,
            },
        ],
        "warnings": [],
        "summary": "remote batch is ready for manifest write and enqueue",
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _validate_enqueue_ack(payload: dict[str, Any], batch_id: str) -> bool:
    return (
        payload.get("batch_id") == batch_id
        and isinstance(payload.get("queue_ref"), str)
        and payload.get("queue_ref")
        and payload.get("status") == "queued"
    )


def _gate_remote_batch(
    load_handoff: dict[str, Any],
    state_path: Path,
    tasks_dir: Path,
    worker_results_dir: Path,
    executor_events_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = _read_json(state_path)
    if state["machine_state"] == "ENQUEUE_REMOTE_BATCH":
        pending_batch = _pending_batch(state)
        batch_id = pending_batch["batch_id"] if pending_batch is not None else _next_batch_id(state)
        enqueue_path = executor_events_dir / f"enqueue-{batch_id}.json"
        if not enqueue_path.exists():
            return _runtime_error(
                output_path,
                "GATE_REMOTE_BATCH",
                "stage enqueue backend response before gating",
                "enqueue acknowledgement missing",
            )
        enqueue_payload = _read_json(enqueue_path)
        if not _validate_enqueue_ack(enqueue_payload, batch_id):
            return _runtime_error(
                output_path,
                "GATE_REMOTE_BATCH",
                "repair staged enqueue backend response",
                "enqueue acknowledgement invalid",
            )
        state.setdefault("remote_batches", []).append(
            {
                "batch_id": batch_id,
                "queue_ref": enqueue_payload["queue_ref"],
                "status": "queued",
            }
        )
        state.pop("pending_remote_batch", None)
        state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
        state["next_action"] = "poll remote batch status"
        _write_json(state_path, state)
        payload = {
            "schema_version": 1,
            "producer": _CONTROL_AGENT,
            "phase": "GATE_REMOTE_BATCH",
            "outcome": "waiting",
            "batch_id": batch_id,
            "manifest_path": None,
            "worker_kind": None,
            "worker_ref": None,
            "task_file": None,
            "recommended_executor_phase": None,
            "recommended_next_machine_state": "WAIT_FOR_REMOTE_BATCH",
            "recommended_next_action": "poll remote batch status",
            "judgment": None,
            "delta": None,
            "executor_directives": [
                {
                    "action": "poll_batch_status",
                    "reason": "batch is queued; poll for status updates",
                    "command": load_handoff["remote_queue"]["status_command"],
                    "batch_id": batch_id,
                },
            ],
            "warnings": [],
            "summary": "enqueue acknowledged and batch is now tracked remotely",
        }
        return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    batch_id = _active_batch_id(state)
    if not batch_id:
        return _runtime_error(
            output_path,
            "GATE_REMOTE_BATCH",
            "repair remote_batches before gating",
            "no active remote batch found",
        )

    status_path = executor_events_dir / f"remote-status-{batch_id}.json"
    if not status_path.exists():
        return _runtime_error(
            output_path,
            "GATE_REMOTE_BATCH",
            "run status_command for active batch",
            "remote status payload missing",
        )
    status_payload = _read_json(status_path)
    if status_payload.get("batch_id") != batch_id or status_payload.get("status") not in {"queued", "running", "completed", "failed"}:
        return _runtime_error(
            output_path,
            "GATE_REMOTE_BATCH",
            "repair staged remote status payload",
            "remote status payload invalid",
        )

    for remote_batch in state["remote_batches"]:
        if remote_batch.get("batch_id") == batch_id:
            remote_batch["status"] = status_payload["status"]
            break

    if status_payload["status"] in {"queued", "running"}:
        state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
        state["next_action"] = "poll remote batch status"
        _write_json(state_path, state)
        payload = {
            "schema_version": 1,
            "producer": _CONTROL_AGENT,
            "phase": "GATE_REMOTE_BATCH",
            "outcome": "waiting",
            "batch_id": batch_id,
            "manifest_path": None,
            "worker_kind": None,
            "worker_ref": None,
            "task_file": None,
            "recommended_executor_phase": None,
            "recommended_next_machine_state": "WAIT_FOR_REMOTE_BATCH",
            "recommended_next_action": "poll remote batch status",
            "judgment": None,
            "delta": None,
            "executor_directives": [
                {
                    "action": "poll_batch_status",
                    "reason": "batch is still in flight; poll for status updates",
                    "command": load_handoff["remote_queue"]["status_command"],
                    "batch_id": batch_id,
                },
            ],
            "warnings": [],
            "summary": "remote batch is still in flight",
        }
        return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    if status_payload["status"] == "completed":
        results_path = executor_events_dir / f"remote-results-{batch_id}.json"
        if not results_path.exists():
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["next_action"] = "fetch remote batch results"
            _write_json(state_path, state)
            payload = {
                "schema_version": 1,
                "producer": _CONTROL_AGENT,
                "phase": "GATE_REMOTE_BATCH",
                "outcome": "fetch_results",
                "batch_id": batch_id,
                "manifest_path": None,
                "worker_kind": None,
                "worker_ref": None,
                "task_file": None,
                "recommended_executor_phase": "FETCH_REMOTE_RESULTS",
                "recommended_next_machine_state": "WAIT_FOR_REMOTE_BATCH",
                "recommended_next_action": "run results_command for active batch",
                "judgment": None,
                "delta": None,
                "executor_directives": [
                    {
                        "action": "fetch_batch_results",
                        "reason": "batch completed; results must be fetched from backend",
                        "command": load_handoff["remote_queue"]["results_command"],
                        "batch_id": batch_id,
                    },
                ],
                "warnings": [],
                "summary": "remote batch completed and results must be fetched",
            }
            return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

        analysis_result_path = worker_results_dir / f"remote-analysis-{batch_id}.json"
        if not analysis_result_path.exists():
            task_path = tasks_dir / f"remote-analysis-{batch_id}.md"
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(_analysis_task_markdown(batch_id, state), encoding="utf-8")
            state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
            state["next_action"] = "run remote results analysis"
            _write_json(state_path, state)
            payload = {
                "schema_version": 1,
                "producer": _CONTROL_AGENT,
                "phase": "GATE_REMOTE_BATCH",
                "outcome": "run_analysis",
                "batch_id": batch_id,
                "manifest_path": None,
                "worker_kind": "custom_agent",
                "worker_ref": "metaopt-analysis-worker",
                "task_file": str(Path(".ml-metaopt") / "tasks" / f"remote-analysis-{batch_id}.md"),
                "recommended_executor_phase": "RUN_REMOTE_ANALYSIS",
                "recommended_next_machine_state": "WAIT_FOR_REMOTE_BATCH",
                "recommended_next_action": "launch remote results analysis worker",
                "judgment": None,
                "delta": None,
                "warnings": [],
                "summary": "remote results are staged and ready for semantic analysis",
            }
            return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

        state["machine_state"] = "ANALYZE_RESULTS"
        state["next_action"] = "analyze remote results"
        _write_json(state_path, state)
        payload = {
            "schema_version": 1,
            "producer": _CONTROL_AGENT,
            "phase": "GATE_REMOTE_BATCH",
            "outcome": "analyze_results",
            "batch_id": batch_id,
            "manifest_path": None,
            "worker_kind": None,
            "worker_ref": None,
            "task_file": None,
            "recommended_executor_phase": None,
            "recommended_next_machine_state": "ANALYZE_RESULTS",
            "recommended_next_action": "analyze remote results",
            "judgment": None,
            "delta": None,
            "warnings": [],
            "summary": "remote results and analysis artifacts are both available",
        }
        return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    diagnosis_path = worker_results_dir / f"remote-diagnosis-{batch_id}.json"
    if not diagnosis_path.exists():
        task_path = tasks_dir / f"remote-diagnosis-{batch_id}.md"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(_diagnosis_task_markdown(batch_id, status_payload), encoding="utf-8")
        state["machine_state"] = "WAIT_FOR_REMOTE_BATCH"
        state["next_action"] = "run remote failure diagnosis"
        _write_json(state_path, state)
        payload = {
            "schema_version": 1,
            "producer": _CONTROL_AGENT,
            "phase": "GATE_REMOTE_BATCH",
            "outcome": "run_remote_diagnosis",
            "batch_id": batch_id,
            "manifest_path": None,
            "worker_kind": "custom_agent",
            "worker_ref": "metaopt-diagnosis-worker",
            "task_file": str(Path(".ml-metaopt") / "tasks" / f"remote-diagnosis-{batch_id}.md"),
            "recommended_executor_phase": "RUN_REMOTE_DIAGNOSIS",
            "recommended_next_machine_state": "WAIT_FOR_REMOTE_BATCH",
            "recommended_next_action": "launch remote failure diagnosis worker",
            "judgment": None,
            "delta": None,
            "warnings": [],
            "summary": "remote batch failed and needs diagnosis before terminal routing",
        }
        return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)

    diagnosis_payload = _read_json(diagnosis_path)
    recommendation = diagnosis_payload.get("fix_recommendation", {})
    action = recommendation.get("action", "abandon")
    state["selected_experiment"]["diagnosis_history"].append(
        {
            "attempt": state["selected_experiment"]["sanity_attempts"],
            "root_cause": diagnosis_payload.get("root_cause"),
            "classification": diagnosis_payload.get("classification"),
            "action": action,
            "code_guidance": recommendation.get("code_guidance"),
            "config_guidance": recommendation.get("config_guidance"),
            "diagnosed_at": _timestamp(),
        }
    )
    for learning in diagnosis_payload.get("learnings", []):
        if learning not in state["key_learnings"]:
            state["key_learnings"].append(learning)

    if action == "adjust_config":
        state["status"] = "BLOCKED_CONFIG"
        state["machine_state"] = "BLOCKED_CONFIG"
        state["next_action"] = recommendation.get("config_guidance") or "repair remote execution configuration"
        outcome = "blocked_config"
        next_state = "BLOCKED_CONFIG"
    else:
        state["status"] = "FAILED"
        state["machine_state"] = "FAILED"
        state["next_action"] = diagnosis_payload.get("root_cause") or "stop after remote execution failure"
        outcome = "failed"
        next_state = "FAILED"

    _write_json(state_path, state)
    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": "GATE_REMOTE_BATCH",
        "outcome": outcome,
        "batch_id": batch_id,
        "manifest_path": None,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "recommended_executor_phase": None,
        "recommended_next_machine_state": next_state,
        "recommended_next_action": state["next_action"],
        "judgment": None,
        "delta": None,
        "warnings": [],
        "summary": "remote failure diagnosis produced a terminal routing decision",
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def _analyze_remote_results(
    state_path: Path,
    worker_results_dir: Path,
    executor_events_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    state = _read_json(state_path)
    batch_id = _active_batch_id(state)
    if not batch_id:
        return _runtime_error(
            output_path,
            "ANALYZE_REMOTE_RESULTS",
            "repair remote_batches before analysis",
            "no completed remote batch found",
        )
    results_path = executor_events_dir / f"remote-results-{batch_id}.json"
    analysis_path = worker_results_dir / f"remote-analysis-{batch_id}.json"
    if not results_path.exists() or not analysis_path.exists():
        return _runtime_error(
            output_path,
            "ANALYZE_REMOTE_RESULTS",
            "stage remote results and analysis payloads before analysis handoff",
            "remote analysis inputs missing",
        )
    results_payload = _read_json(results_path)
    analysis_payload = _read_json(analysis_path)
    if results_payload.get("batch_id") != batch_id or results_payload.get("status") != "completed":
        return _runtime_error(
            output_path,
            "ANALYZE_REMOTE_RESULTS",
            "repair staged remote results payload",
            "remote results payload invalid",
        )
    if analysis_payload.get("judgment") not in {"improvement", "regression", "neutral"}:
        return _runtime_error(
            output_path,
            "ANALYZE_REMOTE_RESULTS",
            "repair staged remote analysis payload",
            "remote analysis payload invalid",
        )

    state["selected_experiment"]["analysis_summary"] = analysis_payload
    if analysis_payload["judgment"] == "improvement" and _improvement_clears_threshold(state, analysis_payload):
        state["baseline"]["aggregate"] = analysis_payload["new_aggregate"]
        state["baseline"]["by_dataset"] = results_payload["per_dataset"]
        state["no_improve_iterations"] = 0
    else:
        state["no_improve_iterations"] += 1

    for learning in analysis_payload.get("learnings", []):
        if learning not in state["key_learnings"]:
            state["key_learnings"].append(learning)

    state["completed_experiments"].append(
        {
            "batch_id": batch_id,
            "proposal_id": state["selected_experiment"]["proposal_id"],
            "aggregate": analysis_payload["new_aggregate"],
            "judgment": analysis_payload["judgment"],
        }
    )
    state["machine_state"] = "ROLL_ITERATION"
    state["next_action"] = "roll iteration"
    _write_json(state_path, state)

    payload = {
        "schema_version": 1,
        "producer": _CONTROL_AGENT,
        "phase": "ANALYZE_REMOTE_RESULTS",
        "outcome": "analyzed",
        "batch_id": batch_id,
        "manifest_path": None,
        "worker_kind": None,
        "worker_ref": None,
        "task_file": None,
        "recommended_executor_phase": None,
        "recommended_next_machine_state": "ROLL_ITERATION",
        "recommended_next_action": "roll iteration",
        "judgment": analysis_payload["judgment"],
        "delta": analysis_payload["delta"],
        "warnings": [],
        "summary": "remote results analysis updated campaign state and baseline accounting",
    }
    return emit_handoff(output_path, payload, handoff_type=_HANDOFF_TYPE, control_agent=_CONTROL_AGENT)


def main() -> int:
    args = _parse_args()
    load_handoff, _, error = _load_inputs(Path(args.load_handoff), Path(args.state_path))
    if error is not None:
        payload = _runtime_error(Path(args.output), None, error["action"], error["summary"], error["warnings"])
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.mode == "plan_remote_batch":
        payload = _plan_remote_batch(load_handoff, Path(args.state_path), Path(args.output))
    elif args.mode == "gate_remote_batch":
        payload = _gate_remote_batch(
            load_handoff,
            Path(args.state_path),
            Path(args.tasks_dir),
            Path(args.worker_results_dir),
            Path(args.executor_events_dir),
            Path(args.output),
        )
    else:
        payload = _analyze_remote_results(
            Path(args.state_path),
            Path(args.worker_results_dir),
            Path(args.executor_events_dir),
            Path(args.output),
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

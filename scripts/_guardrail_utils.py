"""Shared guardrail validators for launch requests and executor directives."""
from __future__ import annotations

from typing import Any


# --- Semantic-lane fields that indicate worker lane drift ---
# If a worker result contains fields from a different lane, the control agent
# must reject or block rather than propagating cross-lane contamination.
LANE_DRIFT_FIELDS: dict[str, frozenset[str]] = {
    "ideation": frozenset({
        "patch_artifacts",
        "apply_results",
        "code_patch",
        "code_patches",
        "code_changes",
        "fix_recommendations",
    }),
    "design": frozenset({
        "patch_artifacts",
        "apply_results",
    }),
}


# --- Allowed slot modes per slot class ---
# Rollover is inline dispatch (no slot), so it is not a valid auxiliary slot mode.
ALLOWED_SLOT_MODES: dict[str, frozenset[str]] = {
    "background": frozenset({"ideation", "maintenance"}),
    "auxiliary": frozenset({"selection", "design", "materialization", "diagnosis", "analysis"}),
}

# --- Allowed workers (worker_ref values) ---
ALLOWED_WORKERS: frozenset[str] = frozenset({
    "metaopt-ideation-worker",
    "metaopt-selection-worker",
    "metaopt-design-worker",
    "metaopt-materialization-worker",
    "metaopt-diagnosis-worker",
    "metaopt-analysis-worker",
    "metaopt-rollover-worker",
    "repo-audit-refactor-optimize",
})

# --- Deterministic model resolution order per class ---
MODEL_RESOLUTION_ORDER_BY_CLASS: dict[str, tuple[str, ...]] = {
    "general_worker": ("claude-sonnet-4", "gpt-5.4"),
    "strong_reasoner": ("claude-opus-4.6-fast", "gpt-5.4"),
    "strong_coder": ("claude-opus-4.6-fast", "gpt-5.4"),
}

# --- Preferred model per model class ---
PREFERRED_MODEL_BY_CLASS: dict[str, str] = {
    model_class: resolution_order[0]
    for model_class, resolution_order in MODEL_RESOLUTION_ORDER_BY_CLASS.items()
}

# --- Worker dispatch contract ---
WORKER_DISPATCH_POLICY: dict[str, dict[str, frozenset[str] | str]] = {
    "metaopt-ideation-worker": {
        "slot_class": "background",
        "modes": frozenset({"ideation"}),
        "model_classes": frozenset({"general_worker"}),
    },
    "repo-audit-refactor-optimize": {
        "slot_class": "background",
        "modes": frozenset({"maintenance"}),
        "model_classes": frozenset({"general_worker", "strong_coder"}),
    },
    "metaopt-selection-worker": {
        "slot_class": "auxiliary",
        "modes": frozenset({"selection"}),
        "model_classes": frozenset({"strong_reasoner"}),
    },
    "metaopt-design-worker": {
        "slot_class": "auxiliary",
        "modes": frozenset({"design"}),
        "model_classes": frozenset({"strong_reasoner"}),
    },
    "metaopt-materialization-worker": {
        "slot_class": "auxiliary",
        "modes": frozenset({"materialization"}),
        "model_classes": frozenset({"strong_coder"}),
    },
    "metaopt-diagnosis-worker": {
        "slot_class": "auxiliary",
        "modes": frozenset({"diagnosis"}),
        "model_classes": frozenset({"strong_reasoner"}),
    },
    "metaopt-analysis-worker": {
        "slot_class": "auxiliary",
        "modes": frozenset({"analysis"}),
        "model_classes": frozenset({"strong_reasoner"}),
    },
    # metaopt-rollover-worker uses inline dispatch (no slot_class), so it has no
    # slot-based dispatch policy entry.  The worker_ref is still in ALLOWED_WORKERS.
}

# --- Allowed executor directive actions ---
ALLOWED_DIRECTIVE_ACTIONS: frozenset[str] = frozenset({
    "apply_patch_artifacts",
    "cancel_slots",
    "delete_state_file",
    "drain_slots",
    "emit_final_report",
    "emit_iteration_report",
    "enqueue_batch",
    "fetch_batch_results",
    "package_code_artifact",
    "package_data_manifest",
    "poll_batch_status",
    "remove_agents_hook",
    "run_sanity",
    "write_manifest",
})

# Actions that represent raw-cluster bypass and must always be rejected.
_BLOCKED_DIRECTIVE_ACTIONS: frozenset[str] = frozenset({
    "ssh_command",
    "raw_ssh",
    "shell_exec",
    "kubectl_exec",
})

REQUIRED_LAUNCH_REQUEST_FIELDS: tuple[str, ...] = (
    "worker_ref",
    "model_class",
    "task_file",
    "result_file",
)

DIRECTIVE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "apply_patch_artifacts": ("result_file", "target_worktree"),
    "cancel_slots": ("slot_ids",),
    "delete_state_file": ("state_path",),
    "drain_slots": ("drain_window_seconds",),
    "emit_final_report": ("report_type",),
    "emit_iteration_report": ("report_type", "iteration"),
    "enqueue_batch": ("command", "manifest_path", "batch_id"),
    "fetch_batch_results": ("command", "batch_id"),
    "package_code_artifact": ("worktree", "code_roots"),
    "package_data_manifest": ("worktree", "data_roots"),
    "poll_batch_status": ("command", "batch_id"),
    "remove_agents_hook": ("agents_path",),
    "run_sanity": ("worktree", "command", "max_duration_seconds"),
    "write_manifest": ("manifest_path", "batch_id"),
}


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _validate_required_fields(entry: dict[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if field not in entry:
            raise ValueError(f"{label}: missing required field {field!r}")
        value = entry[field]
        if isinstance(value, str) and not value:
            raise ValueError(f"{label}: field {field!r} must be a non-empty string")


def normalize_launch_requests(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate and enrich launch requests.

    Rejects illegal slot/mode/worker/model combinations.
    Adds ``preferred_model`` when absent.
    Returns an empty list when *raw* is ``None``.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"launch_requests must be a list, got {type(raw).__name__}")

    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(f"launch_requests[{i}] must be a dict, got {type(entry).__name__}")

        _validate_required_fields(
            entry,
            REQUIRED_LAUNCH_REQUEST_FIELDS,
            label=f"launch_requests[{i}]",
        )

        slot_class = entry.get("slot_class")
        mode = entry.get("mode")
        if slot_class is not None:
            if slot_class not in ALLOWED_SLOT_MODES:
                raise ValueError(
                    f"launch_requests[{i}]: unknown slot_class {slot_class!r}"
                )
            if not _is_non_empty_string(mode):
                raise ValueError(
                    f"launch_requests[{i}]: mode is required when slot_class is provided"
                )
            if mode not in ALLOWED_SLOT_MODES[slot_class]:
                raise ValueError(
                    f"launch_requests[{i}]: mode {mode!r} is not allowed "
                    f"for slot_class {slot_class!r} "
                    f"(allowed: {sorted(ALLOWED_SLOT_MODES[slot_class])})"
                )
        elif mode is not None:
            raise ValueError(
                f"launch_requests[{i}]: slot_class is required when mode is provided"
            )

        worker_ref = entry.get("worker_ref")
        if worker_ref not in ALLOWED_WORKERS:
            raise ValueError(
                f"launch_requests[{i}]: unknown worker_ref {worker_ref!r}"
            )

        model_class = entry.get("model_class")
        if model_class not in PREFERRED_MODEL_BY_CLASS:
            raise ValueError(
                f"launch_requests[{i}]: unknown model_class {model_class!r}"
            )

        dispatch_policy = WORKER_DISPATCH_POLICY.get(worker_ref)
        if dispatch_policy is not None and slot_class is not None:
            expected_slot_class = dispatch_policy["slot_class"]
            if slot_class != expected_slot_class:
                raise ValueError(
                    f"launch_requests[{i}]: worker_ref {worker_ref!r} requires "
                    f"slot_class {expected_slot_class!r}, got {slot_class!r}"
                )

            allowed_modes = dispatch_policy["modes"]
            if mode not in allowed_modes:
                raise ValueError(
                    f"launch_requests[{i}]: worker_ref {worker_ref!r} requires one of "
                    f"{sorted(allowed_modes)}, got {mode!r}"
                )

            allowed_model_classes = dispatch_policy["model_classes"]
            if model_class not in allowed_model_classes:
                raise ValueError(
                    f"launch_requests[{i}]: worker_ref {worker_ref!r} requires one of "
                    f"{sorted(allowed_model_classes)} model classes, got {model_class!r}"
                )

        # Enrich with preferred_model when absent.
        if "preferred_model" not in entry and model_class in PREFERRED_MODEL_BY_CLASS:
            entry["preferred_model"] = PREFERRED_MODEL_BY_CLASS[model_class]

    return raw


def validate_executor_policy(
    control_agent: str,
    handoff_type: str | None,
    directives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject unsupported executor actions, especially raw-cluster bypasses.

    This runs *after* ``normalize_directives()`` has verified structural
    validity (action/reason presence).  It enforces semantic policy.
    """
    for i, entry in enumerate(directives):
        action = entry.get("action", "")

        if action in _BLOCKED_DIRECTIVE_ACTIONS:
            raise ValueError(
                f"executor_directives[{i}]: action {action!r} is a blocked "
                f"raw-cluster bypass (control_agent={control_agent!r}, "
                f"handoff_type={handoff_type!r})"
            )

        if action not in ALLOWED_DIRECTIVE_ACTIONS:
            raise ValueError(
                f"executor_directives[{i}]: action {action!r} is not in "
                f"ALLOWED_DIRECTIVE_ACTIONS (control_agent={control_agent!r}, "
                f"handoff_type={handoff_type!r})"
            )

        required_fields = DIRECTIVE_REQUIRED_FIELDS.get(action, ())
        _validate_required_fields(
            entry,
            required_fields,
            label=f"executor_directives[{i}]",
        )

    return directives


def check_lane_drift(lane: str, result: dict[str, Any]) -> list[str]:
    """Return sorted list of forbidden fields found in *result* for *lane*.

    An empty list means no drift was detected.
    """
    forbidden = LANE_DRIFT_FIELDS.get(lane, frozenset())
    return sorted(forbidden & result.keys())

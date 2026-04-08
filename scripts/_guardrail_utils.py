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
ALLOWED_SLOT_MODES: dict[str, frozenset[str]] = {
    "background": frozenset({"ideation", "maintenance"}),
    "auxiliary": frozenset({"selection", "design", "materialization", "diagnosis", "analysis", "rollover"}),
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

# --- Preferred model per model class ---
PREFERRED_MODEL_BY_CLASS: dict[str, str] = {
    "general_worker": "claude-sonnet-4",
    "strong_reasoner": "claude-opus-4.6-fast",
    "strong_coder": "claude-opus-4.6-fast",
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

        slot_class = entry.get("slot_class")
        if slot_class is not None:
            if slot_class not in ALLOWED_SLOT_MODES:
                raise ValueError(
                    f"launch_requests[{i}]: unknown slot_class {slot_class!r}"
                )
            mode = entry.get("mode")
            if mode is not None and mode not in ALLOWED_SLOT_MODES[slot_class]:
                raise ValueError(
                    f"launch_requests[{i}]: mode {mode!r} is not allowed "
                    f"for slot_class {slot_class!r} "
                    f"(allowed: {sorted(ALLOWED_SLOT_MODES[slot_class])})"
                )

        worker_ref = entry.get("worker_ref")
        if worker_ref is not None and worker_ref not in ALLOWED_WORKERS:
            raise ValueError(
                f"launch_requests[{i}]: unknown worker_ref {worker_ref!r}"
            )

        model_class = entry.get("model_class")
        if model_class is not None and model_class not in PREFERRED_MODEL_BY_CLASS:
            raise ValueError(
                f"launch_requests[{i}]: unknown model_class {model_class!r}"
            )

        # Enrich with preferred_model when absent.
        if "preferred_model" not in entry and model_class in PREFERRED_MODEL_BY_CLASS:
            entry["preferred_model"] = PREFERRED_MODEL_BY_CLASS[model_class]

    return raw


def validate_executor_policy(
    control_agent: str,
    phase: str | None,
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
                f"phase={phase!r})"
            )

        if action not in ALLOWED_DIRECTIVE_ACTIONS:
            raise ValueError(
                f"executor_directives[{i}]: action {action!r} is not in "
                f"ALLOWED_DIRECTIVE_ACTIONS (control_agent={control_agent!r}, "
                f"phase={phase!r})"
            )

    return directives


def check_lane_drift(lane: str, result: dict[str, Any]) -> list[str]:
    """Return sorted list of forbidden fields found in *result* for *lane*.

    An empty list means no drift was detected.
    """
    forbidden = LANE_DRIFT_FIELDS.get(lane, frozenset())
    return sorted(forbidden & result.keys())

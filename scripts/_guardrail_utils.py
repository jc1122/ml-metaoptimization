"""Shared guardrail validators for launch requests and executor directives."""
from __future__ import annotations

from typing import Any


# --- Allowed slot modes per slot class ---
# v4: only ideation (background) and analysis (auxiliary) remain.
# All other execution is via directives to skypilot-wandb-worker.
ALLOWED_SLOT_MODES: dict[str, frozenset[str]] = {
    "background": frozenset({"ideation"}),
    "auxiliary": frozenset({"analysis"}),
}

# --- Allowed workers (worker_ref values) ---
ALLOWED_WORKERS: frozenset[str] = frozenset({
    "metaopt-ideation-worker",
    "metaopt-analysis-worker",
    "skypilot-wandb-worker",
})

# --- Deterministic model resolution order per class ---
MODEL_RESOLUTION_ORDER_BY_CLASS: dict[str, tuple[str, ...]] = {
    "general_worker": ("claude-sonnet-4", "gpt-5.4"),
    "strong_reasoner": ("claude-opus-4.6", "gpt-5.4"),
}

# --- Preferred model per model class ---
PREFERRED_MODEL_BY_CLASS: dict[str, str] = {
    model_class: resolution_order[0]
    for model_class, resolution_order in MODEL_RESOLUTION_ORDER_BY_CLASS.items()
}

# --- Worker dispatch contract ---
# skypilot-wandb-worker has no slot_class — it is dispatched via directive, not launch_requests.
WORKER_DISPATCH_POLICY: dict[str, dict[str, Any]] = {
    "metaopt-ideation-worker": {
        "slot_class": "background",
        "modes": frozenset({"ideation"}),
        "model_classes": frozenset({"general_worker"}),
    },
    "metaopt-analysis-worker": {
        "slot_class": "auxiliary",
        "modes": frozenset({"analysis"}),
        "model_classes": frozenset({"strong_reasoner"}),
    },
    "skypilot-wandb-worker": {
        "slot_class": None,  # directive-dispatched only
        "modes": frozenset(),
        "model_classes": frozenset({"general_worker"}),
    },
}

# --- Allowed directive actions ---
# Blocked: queue_op, apply_patch_artifacts, package_code_artifact,
#          package_data_manifest, write_manifest, run_sanity,
#          drain_slots, cancel_slots, ssh_command, raw_ssh, kubectl_exec
ALLOWED_DIRECTIVE_ACTIONS: frozenset[str] = frozenset({
    "launch_sweep",
    "poll_sweep",
    "run_smoke_test",
    "remove_agents_hook",
    "delete_state_file",
    "emit_final_report",
    "emit_iteration_report",
    "none",
})

# --- Required fields per directive action ---
DIRECTIVE_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "launch_sweep": frozenset({"sweep_config", "sky_task_spec", "result_file"}),
    "poll_sweep": frozenset({"sweep_id", "sky_job_ids", "result_file"}),
    "run_smoke_test": frozenset({"command", "result_file"}),
    "remove_agents_hook": frozenset({"agents_path"}),
    "delete_state_file": frozenset({"state_path"}),
    "emit_final_report": frozenset({"report_type"}),
    "emit_iteration_report": frozenset({"report_type", "iteration"}),
    "none": frozenset(),
}

# --- Blocked actions (raw cluster bypass) ---
BLOCKED_DIRECTIVE_ACTIONS: frozenset[str] = frozenset({
    "ssh_command",
    "raw_ssh",
    "kubectl_exec",
    "queue_op",
    "apply_patch_artifacts",
    "package_code_artifact",
    "package_data_manifest",
    "write_manifest",
    "run_sanity",
    "drain_slots",
    "cancel_slots",
})

_LAUNCH_REQUEST_REQUIRED_FIELDS = frozenset({
    "worker_ref",
    "model_class",
    "task_file",
    "result_file",
})
_SLOT_REQUIRED_FIELDS = frozenset({"slot_id", "slot_class", "mode"})


def normalize_launch_requests(launch_requests: Any) -> list[dict[str, Any]]:
    """Validate and normalize a launch_requests list from a control-agent handoff.

    Adds ``preferred_model`` to each entry if not already present.
    Raises TypeError for structural problems, ValueError for semantic violations.
    """
    if launch_requests is None:
        return []
    if not isinstance(launch_requests, list):
        raise TypeError(f"launch_requests must be a list, got {type(launch_requests).__name__}")

    result = []
    for entry in launch_requests:
        if not isinstance(entry, dict):
            raise TypeError(f"each launch_requests entry must be a dict, got {type(entry).__name__}")

        entry = dict(entry)

        # Validate required base fields
        missing = _LAUNCH_REQUEST_REQUIRED_FIELDS - entry.keys()
        if missing:
            raise ValueError(f"launch_requests entry missing required fields: {sorted(missing)}")

        worker_ref = entry["worker_ref"]
        if worker_ref not in ALLOWED_WORKERS:
            raise ValueError(
                f"unknown worker_ref {worker_ref!r}; allowed: {sorted(ALLOWED_WORKERS)}"
            )

        model_class = entry["model_class"]
        if model_class not in MODEL_RESOLUTION_ORDER_BY_CLASS:
            raise ValueError(
                f"unknown model_class {model_class!r}; allowed: "
                f"{sorted(MODEL_RESOLUTION_ORDER_BY_CLASS)}"
            )

        policy = WORKER_DISPATCH_POLICY[worker_ref]

        # Reject directive-dispatched workers from all slot-based launch_requests
        if policy["slot_class"] is None:
            raise ValueError(
                f"worker {worker_ref!r} is directive-dispatched and must not appear in "
                "launch_requests; dispatch it via directive instead"
            )

        has_slot_class = "slot_class" in entry
        has_mode = "mode" in entry

        if has_slot_class != has_mode:
            if not has_slot_class:
                raise ValueError("slot_class is required when mode is present")
            raise ValueError("mode is required when slot_class is present")

        if has_slot_class:
            slot_class = entry["slot_class"]
            mode = entry["mode"]

            if slot_class not in ALLOWED_SLOT_MODES:
                raise ValueError(
                    f"unknown slot_class {slot_class!r}; allowed: {sorted(ALLOWED_SLOT_MODES)}"
                )

            if slot_class != policy["slot_class"]:
                raise ValueError(
                    f"worker {worker_ref!r} requires slot_class {policy['slot_class']!r}, "
                    f"got {slot_class!r}"
                )

            allowed_modes = ALLOWED_SLOT_MODES[slot_class]
            if mode not in allowed_modes:
                raise ValueError(
                    f"slot_class {slot_class!r} does not allow mode {mode!r}; "
                    f"allowed: {sorted(allowed_modes)}"
                )

            if mode not in policy["modes"]:
                raise ValueError(
                    f"worker {worker_ref!r} requires one of modes {sorted(policy['modes'])}, "
                    f"got {mode!r}"
                )

            if model_class not in policy["model_classes"]:
                raise ValueError(
                    f"worker {worker_ref!r} requires model classes "
                    f"{sorted(policy['model_classes'])}, got {model_class!r}"
                )

        # Inject preferred_model if not set
        if "preferred_model" not in entry:
            entry["preferred_model"] = PREFERRED_MODEL_BY_CLASS[model_class]

        result.append(entry)

    return result


def validate_executor_policy(
    agent_name: str,
    phase: str | None,
    directives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate a list of executor directives from a control-agent handoff.

    Raises ValueError for blocked actions or schema violations.
    Returns the validated directive list unchanged on success.
    """
    for directive in directives:
        action = directive.get("action", "")

        if action in BLOCKED_DIRECTIVE_ACTIONS:
            raise ValueError(
                f"directive action {action!r} is blocked (raw-cluster bypass or v3 artifact "
                f"operation); agent={agent_name!r} phase={phase!r}"
            )

        if action not in ALLOWED_DIRECTIVE_ACTIONS:
            raise ValueError(
                f"unknown directive action {action!r}; allowed: {sorted(ALLOWED_DIRECTIVE_ACTIONS)}"
            )

        required = DIRECTIVE_REQUIRED_FIELDS.get(action, frozenset())
        missing = required - directive.keys()
        if missing:
            raise ValueError(
                f"directive {action!r} missing required fields: {sorted(missing)}"
            )

    return directives

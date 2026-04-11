# Backend Contract

## Purpose

The skill interacts with remote execution **only** through this queue-based contract. The backend owns cluster verification, sync, submission, retries, result collection, and utilization management.

**Execution ownership:** All three queue commands (`enqueue_command`, `status_command`, `results_command`) are executed exclusively by `metaopt-remote-execution-control`, which must invoke the `hetzner-delegation` skill before running any of them. The orchestrator never calls queue commands directly — it only writes manifest files (via the `write_manifest` executor directive) and applies state patches returned by the control agent.

**Queue-only rule:** The skill must never execute raw SSH commands, Ray cluster operations, `kubectl exec`, or any direct cluster interaction. All remote execution flows through the three declared queue commands below, called by `metaopt-remote-execution-control` via `hetzner-delegation`. Any attempt to bypass this contract — whether by the orchestrator constructing ad-hoc remote commands or by a control agent emitting raw-cluster executor directives — is a protocol breach and must be rejected. If a control agent emits an executor directive with a blocked action (e.g. `ssh_command`, `raw_ssh`, `shell_exec`, `kubectl_exec`), the guardrail validators reject it before execution. No raw SSH, Ray CLI, or direct cluster probing is permitted from within this skill.

Forbidden fallback examples include `ray job submit`, `ray start`, `ray stop`, `scp`, `rsync`, and cloud-console or cloud-CLI lifecycle detours such as `hcloud`. If the queue backend cannot represent the needed next step, the orchestrator must fail closed to `BLOCKED_PROTOCOL` or route through the diagnosis lane; it must never invent a direct-per-node execution path.

## Required Backend Commands

Declared in `ml_metaopt_campaign.yaml` under `remote_queue`:
- `enqueue_command`
- `status_command`
- `results_command`

`metaopt-remote-execution-control` must call only these commands for remote execution, via the `hetzner-delegation` skill. No other component may invoke them.

These fields are shell command strings, not argv arrays. The backend command contract assumes shell execution semantics, including normal shell path expansion.
The orchestrator appends one shell-escaped value after the command string declared in the campaign file.
The command string must include the flag name so the final invocation is valid:
- `enqueue_command --manifest <manifest_path>` → e.g. `python3 enqueue_batch.py --manifest /path/manifest.json`
- `status_command --batch-id <batch_id>` → e.g. `python3 get_batch_status.py --batch-id batch-001`
- `results_command --batch-id <batch_id>` → e.g. `python3 fetch_batch_results.py --batch-id batch-001`

The current `ray-hetzner` implementation uses `--manifest` and `--batch-id` named flags. The campaign example includes these flags in the command strings accordingly.

All three commands must write exactly one stdout JSON object on success and exit non-zero on failure.

## Enqueue Contract

Input:
- exactly one immutable batch manifest

Output:
- stdout JSON object with:
  - `batch_id`
  - `queue_ref`
  - `status`

Required behavior:
- register the batch
- echo the manifest `batch_id` exactly as provided by the orchestrator
- return a stable `queue_ref`
- set `status = "queued"`
- make queued status observable

## Status Contract

Input:
- exactly one `batch_id`

The backend must expose a stdout JSON object with:
- `batch_id`
- lifecycle `status`
- `timestamps`
- utilization when available
- failure classification when failed
- result pointers when completed

Accepted lifecycle states:
- `queued`
- `running`
- `completed`
- `failed`

## Results Contract

Input:
- exactly one `batch_id`

The backend must expose a stdout JSON object with:
- `batch_id`
- `status`
- `best_aggregate_result`
- `best_aggregate_result.metric`
- `best_aggregate_result.value`
- `per_dataset`
- `artifact_locations`
- `logs_location`

Required results behavior:
- echo the requested `batch_id` exactly
- report `status = "completed"`
- provide `best_aggregate_result.metric` as a non-empty metric name
- provide `best_aggregate_result.value` as the numeric aggregate score for that metric
- return non-empty artifact locations for the immutable code artifact, immutable data manifest, and execution logs

Required artifact location fields:
- `artifact_locations.code`
- `artifact_locations.data_manifest`
- `logs_location`

## Artifact Contract

The backend must accept immutable artifacts, not mutable working tree paths.

Expected artifact behavior:
- consume a content-addressed or fixed manifest reference
- unpack or materialize into an isolated execution workspace
- run the declared entrypoint there

## Retry Policy Contract

The orchestrator declares retry policy in the campaign spec and batch manifest.
The backend must honor the declared retry policy.
If the selected backend cannot honor it, the run must fail before enqueue.

## Utilization Contract

The backend is responsible for cluster utilization policy.

The skill expresses utilization goals in the campaign file, but the backend decides how those goals map to actual cluster jobs or internal trial fanout.

## Current Implementation

Current backend:
- `ray-hetzner`

Mapping:
- enqueue -> `metaopt/enqueue_batch.py`
- status -> `metaopt/get_batch_status.py`
- results -> `metaopt/fetch_batch_results.py`
- backend reconciler -> `metaopt/head_daemon.py`

This mapping is an implementation note, not the generic contract itself.

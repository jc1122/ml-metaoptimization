# Backend Contract

## Purpose

The orchestrator interacts with remote execution only through this contract. The backend owns cluster verification, sync, submission, retries, result collection, and utilization management.

## Required Backend Commands

Declared in `ml_metaopt_campaign.yaml` under `remote_queue`:
- `enqueue_command`
- `status_command`
- `results_command`

The skill may call only these commands for remote execution.

These fields are shell command strings, not argv arrays. The backend command contract assumes shell execution semantics, including normal shell path expansion.
The orchestrator appends one shell-escaped argument to each command:
- `enqueue_command <manifest_path>`
- `status_command <batch_id>`
- `results_command <batch_id>`

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
- `best_aggregate_result`
- `per_dataset`
- `artifact_locations`
- `logs_location`

## Artifact Contract

The backend must accept immutable artifacts, not mutable working tree paths.

Expected artifact behavior:
- consume a content-addressed or fixed manifest reference
- unpack or materialize into an isolated execution workspace
- run the declared entrypoint there

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

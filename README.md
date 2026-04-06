# ML Metaoptimization Skill Repo

This repository defines the `ml-metaoptimization` skill plus its supporting reference contracts and example campaign.
This repository is a contract-only scope for the `ml-metaoptimization` skill.
It pins the public docs, examples, and fixtures for the orchestration runtime.
It does not simulate a live Copilot host or remote queue backend.

## Validation

Install the validation dependency from the repo root:

```bash
python3 -m pip install --user -r requirements.txt
```

Run the validation suite from the repo root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

The tests pin the public contract for:
- the example campaign file
- backend stdout JSON payload shapes
- state-file fixtures and lifecycle pairing rules
- cross-document consistency between the skill and reference docs

## Ecosystem

This orchestrator delegates to the following worker skills:

- [`metaopt-experiment-ideation`](https://github.com/jc1122/metaopt-experiment-ideation) — proposal generation
- [`metaopt-experiment-selection`](https://github.com/jc1122/metaopt-experiment-selection) — proposal ranking and winner selection
- [`metaopt-experiment-design`](https://github.com/jc1122/metaopt-experiment-design) — experiment batch specification
- [`metaopt-experiment-materialization`](https://github.com/jc1122/metaopt-experiment-materialization) — code changes and patch artifacts
- [`metaopt-sanity-diagnosis`](https://github.com/jc1122/metaopt-sanity-diagnosis) — failure diagnosis
- [`metaopt-results-analysis`](https://github.com/jc1122/metaopt-results-analysis) — results evaluation and learning extraction
- [`metaopt-proposal-rollover`](https://github.com/jc1122/metaopt-proposal-rollover) — iteration transition filtering

Backend execution:
- [`hetzner-delegation`](https://github.com/jc1122/hetzner-delegation) — remote compute delegation skill
- [`ray-hetzner`](https://github.com/jc1122/ray-hetzner) — Ray cluster and queue runtime

Maintenance:
- [`repo-audit-refactor-optimize`](https://github.com/jc1122/repo-audit-refactor-optimize) — background maintenance lane

## Runtime Notes

`SKILL.md` describes the intended GitHub Copilot-style orchestration runtime.

`agents/openai.yaml` is separate catalog metadata for OpenAI/Codex-style runtimes. It exists so those runtimes can surface this skill in their own UI/catalog flows; it does not define Copilot dispatch behavior.

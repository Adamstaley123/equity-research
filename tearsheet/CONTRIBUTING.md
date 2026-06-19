# Contributing to Tearsheet

Thanks for your interest! Tearsheet aims to be a trustworthy, transparent
equity-research tool — contributions that improve data accuracy, coverage, and
clarity are especially welcome.

## Setup

```bash
cd tearsheet
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest        # should be green
```

## Good first contributions

- **Add a sector** — drop a `sectors/<name>.yaml` (see `sectors/payments.yaml`).
  PRs that add well-researched sector universes are great.
- **Concept coverage** — if a company shows `N/A` because a metric is tagged
  under an XBRL concept we don't try yet, add it to the relevant list in
  `pipeline/config.py` (the staleness guard makes this safe).
- **Fix a data edge case** — found a wrong/misleading number? Open an issue with
  the ticker and metric, or a PR with a test that reproduces it.

## Ground rules

- **Keep the data path deterministic.** No LLMs or non-reproducible sources in
  the pipeline. Same inputs must yield the same output.
- **Every value keeps its provenance.** New fetchers/metrics must populate
  `source`, `period`, and a real `na_reason` when unavailable — never a silent
  blank or a placeholder number.
- **Pure functions stay pure.** `compute.py` does no I/O; add a unit test for any
  new metric or guard in `tests/test_compute.py`.
- **Run the tests.** `pytest` must pass; the smoke test renders the dashboard
  offline from a fixture, so template changes are covered too.

## Pull requests

1. Branch from `main`.
2. Keep changes focused; match the surrounding style.
3. Add/adjust tests; run `pytest`.
4. Describe what changed and why. If it changes a displayed number, show the
   before/after and the source.

## Reporting issues

Include the sector, ticker(s), the metric, what you expected, and what you got
(a screenshot or the expanded detail card helps). Data-accuracy reports are high
priority.

# Quant-Agent End-to-End Evals

This suite evaluates complete Agent behavior separately from unit and integration tests. Each YAML scenario declares the expected graph outcome, task-success meaning, ordered tool subsequences, forbidden tools, approval decisions, evidence, and allowed file mutations.

The default provider is deterministic `mock`; no API key or network call is required. Run all scenarios with:

```powershell
.\.venv311\Scripts\python.exe -m evals.run
```

Run one scenario with `--scenario <id>`. Results are written as machine-readable `results.json` and a human-readable `summary.md` under `evals/results/<suite-id>/`. Runtime fixture directories are deleted unless `--keep-workdirs` is supplied.

Ordered subsequences are the default sequence grader. Exact tool sequences are reserved for scenarios where extra actions would violate the workflow invariant.


# Quant Agent MCP Tool Guide

## Read project state

- `read_project_state`: current implementation stage, latest verified run, gaps, and next steps. Read-only.
- `read_capabilities`: implemented, partial, and planned capability names. Read-only.

## Inspect evidence

- `inspect_latest_run`: latest run metadata, metrics summary, artifact status, and promotion status. Read-only.
- `metrics_latest`: compact performance metrics. Read-only.
- `inspect_artifacts_latest`: rerun and save the artifact contract inspection. Writes audit files.
- `promote_latest`: regenerate and save promotion evidence. Writes audit files.
- `inspect_proposal`: summarize a proposal and its patch/apply status. Read-only.
- `query_code_memory`: hybrid semantic, lexical, symbol, and path retrieval with source citations. Read-only, but semantic query embeddings may call the configured provider.

## Prepare or revise a proposal

- `suggest_revision`: write a revision plan from failed verification evidence.
- `synthesize_revision`: create a structured fix proposal only for supported failure shapes.

## Approval-gated operations

- `trace_source`: inspect a target source file and write trace artifacts.
- `suggest_fix`: draft a structured fix proposal; generalized proposals may call an LLM provider.
- `apply_fix_dry_run`: validate a reviewed proposal without modifying its target repository.
- `apply_fix_yes`: modify the proposal's exact target file. This is destructive and requires explicit approval.
- `run_fresh`: launch the configured external backtest. This can be long-running and can access external systems configured by that backtest.

Pass project-relative paths where practical. Treat tool errors as evidence; do not retry a failing write with broader paths or weaker safeguards.

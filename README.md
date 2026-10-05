# Quant Agent

[![CI](https://github.com/TianleiP/quant_research_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/TianleiP/quant_research_agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A lightweight quant research workstation for running, recording, auditing, and documenting strategy experiments.

This repository starts with a deterministic CLI scaffold. The agent treats the backtester as an adapter dependency: strategy-specific output is normalized into stable artifacts that reports, replay, audits, and LLM-assisted diagnostics can consume.

## Quick Start

Use Python 3.11 for the quant-agent runtime:

```bash
.venv311/Scripts/python.exe -m pip install -e ".[dev]"
```

SQLite checkpointing is included in the default install. To develop or run
the optional PostgreSQL checkpoint backend, install the PostgreSQL extra:

```bash
.venv311/Scripts/python.exe -m pip install -e ".[dev,postgres]"
```

### Five-minute repair demo

The repository includes a self-contained external strategy fixture, so the
complete repair workflow can be demonstrated without the author's private
research repository or an API key:

```powershell
.venv311\Scripts\python.exe scripts\run_repair_demo.py --approve-demo-actions
```

The command creates an isolated copy under `.demo_runs/`, starts from a run
that is missing positions, trades, and rankings, and uses the real
`plan-react` runner to retrieve code, trace the export path, prepare a patch,
pause at five LangGraph approval interrupts, apply the reviewed repair, and
verify a fresh artifact set. The flag explicitly authorizes only those five
expected demo actions; anything unexpected is rejected. The generated
`demo_report.json` and `demo_report.md` record the tool sequence, approvals,
checkpoint evidence, and deterministic success checks. See
[`examples/README.md`](examples/README.md) for the fixture boundary.

Machine-local integration files are intentionally excluded from version
control. Copy the public templates only when you need those integrations, then
replace their placeholder paths for your workstation:

```powershell
Copy-Item configs/xgboost_live_strategy.example.yaml configs/xgboost_live_strategy.yaml
Copy-Item state/project_state.example.json state/project_state.json
Copy-Item .codex/config.example.toml .codex/config.toml
```

```bash
.venv311/Scripts/quant-agent.exe run --config configs/example_strategy.yaml
.venv311/Scripts/quant-agent.exe metrics --run latest
.venv311/Scripts/quant-agent.exe replay --run latest --date 2021-03-08
.venv311/Scripts/quant-agent.exe audit costs --run latest
.venv311/Scripts/quant-agent.exe close-promotion --run latest --diagnose --provider mock
```

`replay` reads the decision log plus available position-level artifacts, so newer runs include actual holdings, trade changes, and ranking context for the requested date.

To route a plain-English request to the right command:

```bash
.venv311/Scripts/quant-agent.exe ask "show latest metrics"
.venv311/Scripts/quant-agent.exe ask "why did it trade on 2000-03-13"
.venv311/Scripts/quant-agent.exe ask "apply the latest fix proposal" --provider mock
```

By default, `ask` prints the route plan without executing it. Add `--execute` to run the routed command. Requests that could trace, patch, apply, install, or rerun backtests are routed through `agent-session` so the existing approval gates remain in force.

To ingest the current external XGBoost research output without launching the long backtest, use:

```bash
.venv311/Scripts/quant-agent.exe run --config configs/xgboost_live_strategy.yaml
```

That config uses `engine: subprocess_csv` with `run_before_ingest: false`. Flip it to `true` when you want quant-agent to run the external script first and ingest the refreshed CSVs afterward.

You can also request a fresh external run without editing the YAML:

```bash
.venv311/Scripts/quant-agent.exe run --config configs/xgboost_live_strategy.yaml --fresh
```

The quant-agent runtime and external backtest runtime are intentionally separate. The agent runs on Python 3.11, while `configs/xgboost_live_strategy.yaml` runs the XGBoost research script through its configured Python 3.7 subprocess.

To smoke-test the LLM diagnosis path without an API key:

```bash
.venv311/Scripts/quant-agent.exe diagnose --run latest --provider mock --format both
```

To call OpenAI, set an API key and run:

```powershell
$env:OPENAI_API_KEY="<your-key>"
.venv311/Scripts/quant-agent.exe diagnose --run latest --provider openai
```

The default OpenAI model is `gpt-5.1-mini`; override it with `--model` or `QUANT_AGENT_LLM_MODEL`.

To call DeepSeek, set an API key and run:

```powershell
$env:DEEPSEEK_API_KEY="<your-key>"
.venv311/Scripts/quant-agent.exe diagnose --run latest --provider deepseek
```

The default DeepSeek model is `deepseek-v4-flash`; override it with `--model` or `QUANT_AGENT_DEEPSEEK_MODEL`.

`diagnose` supports `--format markdown`, `--format json`, and `--format both`.
The default is `both`, which writes `diagnosis.json` and a rendered
`diagnosis.md`.

To stress a run for transaction costs and slippage:

```bash
.venv311/Scripts/quant-agent.exe audit costs --run latest
```

The cost audit uses daily turnover when available, falls back to aggregate `turnover_per_year` otherwise, and writes `audits/cost_stress.json` plus `audits/cost_stress.md`.

To record or view strategy lineage:

```bash
.venv311/Scripts/quant-agent.exe lineage show
.venv311/Scripts/quant-agent.exe lineage update --strategy base5_combo_accel2_mom12 --previous base8_mom12_accel2_mom12 --changed-rule "Use promoted live variant."
```

Lineage is stored in `state/strategy_lineage.json` and is included in promotion reports.

To close the promotion review loop:

```bash
.venv311/Scripts/quant-agent.exe close-promotion --run latest --diagnose --provider mock
```

This regenerates promotion evidence, optionally attaches diagnosis, and writes `audits/promotion_closure.json` plus `audits/promotion_closure.md`. The closure report classifies each remaining warning as closed, needing action, needing a human risk decision, or blocked.

To run the first LangGraph-backed agent loop:

```bash
.venv311/Scripts/quant-agent.exe agent-session --task "summarize current project status"
```

The default `agent-session` implementation uses a deterministic rule planner. To use structured LLM action selection:

```bash
.venv311/Scripts/quant-agent.exe agent-session --task "inspect artifact and promotion status with metrics" --planner llm --provider mock
```

The LLM planner must return strict JSON and can only choose from the action registry. It can read project state, read the capability map, inspect the latest run, display latest metrics, inspect artifact contracts, generate a promotion report, or propose approval-gated trace/fix/run actions.

To use the native function/tool-calling ReAct executor:

```bash
.venv311/Scripts/quant-agent.exe agent-session --task "inspect artifact and promotion status with metrics" --planner react --provider mock
```

The ReAct path sends JSON-schema tool definitions to OpenAI Responses, DeepSeek Chat Completions, or the deterministic mock provider. The model chooses one tool, quant-agent executes it, and the result is returned to the model with the matching tool-call id. This repeats until the model returns a final answer. Tool risk is never trusted to the model: the shared tool catalog marks approval-gated operations, and every selected action still passes through the deterministic validation and approval layer before execution. The older `--planner llm` JSON selector remains available for compatibility.

To add an explicit multi-step plan and bounded self-correction around the ReAct executor:

```bash
.venv311/Scripts/quant-agent.exe agent-session --task "inspect artifact and promotion status with metrics" --planner plan-react --provider mock --max-steps 5 --max-replans 2
```

`plan-react` asks the provider for a strict structured plan, stores that plan in LangGraph State, and exposes only the current step's allowed tools to the executor. After each meaningful tool result, a structured evaluator marks the current step complete, continues it, blocks it, or requests a revised remaining plan. Replanning is capped by `--max-replans`; evaluation is capped by the session action budget. A model cannot mark an unrelated or unexecuted step complete, and every selected tool still passes through the same deterministic argument, risk, and approval checks. Plan versions and evaluation decisions are preserved in both the checkpointed state and JSONL audit log.

Before an approval-gated trace/fix/run action is shown for approval, `agent-session` fills common missing arguments from `state/project_state.json`, the latest run metadata/audits, matching repo code-memory indexes, and the latest `.fix_proposals/*/proposal.json`. This lets a model choose `trace_source`, `suggest_fix`, `inspect_proposal`, `apply_fix_*`, or `run_fresh` without hardcoding the current repo/file/proposal paths in its response.

Approval-gated actions pause the session and write:

```text
state/sessions/<session_id>.approval.json
state/sessions/<session_id>.state.json
state/sessions/<session_id>.jsonl
state/sessions/checkpoints.sqlite       # default SQLite backend only
```

The LangGraph checkpoint is the resumable execution source of truth. The state
JSON is a readable snapshot, and the JSONL session file remains the audit log.
SQLite is the default; PostgreSQL is optional. Both preserve the same
`thread_id`, `interrupt()`, and `Command(resume=...)` workflow.

### Checkpoint backends

No configuration is required for local SQLite development:

```bash
.venv311/Scripts/quant-agent.exe agent-session --task "summarize current project status"
```

For a local PostgreSQL checkpoint store, copy the PostgreSQL entries from
`.env.example` into the ignored `.env`, replace `POSTGRES_PASSWORD=replace-me`,
and use the same password in both PostgreSQL URIs. Then start the local-only
database:

```bash
docker compose up -d --wait checkpoint-postgres
docker compose ps
```

The Compose port is bound to `127.0.0.1:55432`; it is not exposed on every
network interface. Start an Agent session with either the `.env` backend value
or the explicit CLI selector:

```bash
.venv311/Scripts/quant-agent.exe agent-session --task "trace source" --planner llm --provider mock --checkpoint-backend postgres
```

Resume uses the same `thread_id`. When the readable state snapshot is present,
the runner can infer its backend. If only the checkpoint survives, provide the
backend through `.env`, the environment, or `--checkpoint-backend postgres`:

```bash
.venv311/Scripts/quant-agent.exe agent-resume --session <thread_id> --approve --checkpoint-backend postgres
```

Run the optional backend parity tests against a disposable database:

```powershell
.venv311/Scripts/python.exe -m pytest -m postgres tests/integration/test_checkpoint_backend_parity.py
```

The test reads `QUANT_AGENT_TEST_POSTGRES_URI` from the process environment or
the project `.env` file.

The factory uses the official `langgraph-checkpoint-postgres` schema and calls
its idempotent `setup()` migration method. PostgreSQL connection URIs are not
written to state snapshots or JSONL audit events. Stop the local database with
`docker compose down`; omit `-v` if you want to keep its checkpoint volume.

PostgreSQL improves deployment and multi-process persistence options. It does
not make the Agent more intelligent, make arbitrary external side effects
exactly-once, or replace the existing deterministic safety and approval layer.

Resume a paused session with:

```bash
.venv311/Scripts/quant-agent.exe agent-resume --session <session_id> --approve
.venv311/Scripts/quant-agent.exe agent-resume --session <session_id> --reject
```

Current approval-gated actions include source tracing, fix proposal generation, fix dry-runs, reviewed fix application, and fresh configured runs.

To run the full proposal lifecycle through the agent loop:

```bash
.venv311/Scripts/quant-agent.exe agent-session --task "complete latest proposal patch-run-inspect lifecycle" --max-steps 10
```

This inspects the latest proposal, asks for approval before the dry-run, asks again before applying the patch, asks again before the fresh verification run, then inspects the resulting artifacts and latest run.

If verification fails or artifact inspection is not satisfied, the lifecycle writes a non-applicable revision proposal with `failure_context.md` under `.fix_proposals/`. For supported missing position/trade/ranking export failures, it also creates a revised missing-export proposal; that revised proposal includes an auto-applicable patch only when the source matches a known safe pattern. When a revised proposal is applicable, the lifecycle restarts from it and continues through the same approval-gated dry-run/apply/verify sequence. A loop guard blocks repeated revised proposals or repeated failure signatures.

## Use the same tools from Codex through MCP

The project now includes a local stdio MCP server:

```bash
.venv311/Scripts/quant-agent-mcp.exe --root C:/path/to/quant-agent
```

Register it once for this Codex installation with:

```bash
codex mcp add quant_agent -- C:/path/to/quant-agent/.venv311/Scripts/python.exe -m quant_agent.mcp_server --root C:/path/to/quant-agent --enable-approval-gated
```

The server publishes the same 15 JSON-schema tools used by `agent-session`; it does not reimplement the quant workflows. Tool descriptions and read/write/destructive annotations come from the central tool catalog, and calls return structured JSON. Approval-gated operations are disabled by default. A machine-local `.codex/config.toml` can enable them for Codex while setting `default_tools_approval_mode = "writes"`, so the host asks before write operations and the existing deterministic registry remains the final enforcement layer.

Use `.codex/config.example.toml` as the public template for a project-scoped trusted Codex configuration. The actual `.codex/config.toml` is ignored because it contains workstation paths. Restart Codex after changing either MCP configuration. Then invoke the repository Skill with a request such as:

```text
Use $quant-research-workflow to inspect the latest run and explain the safest next action.
```

The Skill lives at `.agents/skills/quant-research-workflow/`. It teaches Codex the evidence-first sequence `inspect -> diagnose -> propose -> approve -> execute -> verify -> report`; the MCP server performs the actions. The Skill itself cannot bypass approval or path checks.

## Run the end-to-end Agent evals

The separate `evals/` suite runs six isolated, repeatable workflow scenarios with the deterministic mock provider:

```bash
.venv311/Scripts/python.exe -m evals.run
```

It covers read-only assessment with zero project mutation, approval rejection, a successful retrieve/trace/propose/apply/verify repair, failed verification with bounded replanning, repeated-failure loop protection, and parity between the real stdio MCP server and the direct shared tool registry. The repair scenario also uses deterministic fake embeddings to prove that a low-lexical-overlap query can retrieve the correct source through the semantic channel.

Each scenario declares expected graph status, a separate task-success value, terminal reason, required ordered tool subsequences, forbidden tools, approval decisions, evidence, and allowed mutation globs. Deterministic graders compare those contracts with actual actions, checkpoints, files, and tool results. Reports are written to `evals/results/<suite-id>/results.json` and `summary.md`; generated reports are ignored by git.

Run one scenario with `--scenario <id>`. An optional real-provider smoke run is available only for the read-only scenario, for example `--scenario readonly_project_assessment --provider openai`; the default suite makes no paid or network model calls.

To refresh the centralized machine-readable project state:

```bash
.venv311/Scripts/quant-agent.exe update-state --test-status "147 passed"
```

Use `--dry-run` to compute the update without writing `state/project_state.json`.

For local development, you can also place secrets in an ignored `.env` file:

```text
DEEPSEEK_API_KEY=<your-key>
```

Never commit `.env` or real credentials. Before publishing, run the repository
audit; it reports only the finding category, file, and line number, never the
matched secret value:

```bash
.venv311/Scripts/python.exe scripts/publication_audit.py
```

The audit checks publication candidates for likely API tokens, private-key
headers, and personal Windows user paths. Generated evidence, virtual
environments, run data, and the three ignored machine-local configuration files
remain outside the publication boundary.

## Continuous integration

GitHub Actions runs on every push to `main` and on pull requests. The single CI
job installs Python 3.11 with the PostgreSQL optional dependencies, starts a
disposable PostgreSQL 17 service, runs the publication audit, executes the full
unit/integration suite, and then runs all six end-to-end Agent eval scenarios.
The database credential in the workflow is test-only and exists only inside the
ephemeral CI job.

This repository is released under the [MIT License](LICENSE).

To build a structural index of an external backtest repo:

```bash
.venv311/Scripts/quant-agent.exe discover --repo C:/path/to/xgboost
```

Discovery artifacts are written under `.discovery/<repo-name>/`.

To build and query local code memory for source retrieval:

```bash
.venv311/Scripts/quant-agent.exe memory build --repo C:/path/to/xgboost
.venv311/Scripts/quant-agent.exe memory query --index .code_memory/xgboost/code_memory.json --query "where are positions trades rankings exported"
```

The default build remains fully local. It now records lexical term frequencies, Python symbols, paths, and the existing compact hashed vectors, then combines BM25, exact symbol/path evidence, and the hashed fallback with weighted reciprocal-rank fusion.

For true semantic retrieval, explicitly opt in to OpenAI embeddings:

```powershell
$env:OPENAI_API_KEY="<your-key>"
.venv311/Scripts/quant-agent.exe memory build --repo C:/path/to/xgboost --embedding-provider openai --embedding-model text-embedding-3-small --embedding-dimensions 256
.venv311/Scripts/quant-agent.exe memory query --index .code_memory/xgboost/code_memory.json --query "where is portfolio exposure saved"
```

Semantic indexes store a dense embedding beside each source chunk. Query mode `auto` embeds the question with the same model when credentials are available, then fuses semantic cosine similarity with BM25, symbol, and path rankings. Results include per-channel scores and `file:start-end` citations. If credentials are absent or an automatic provider call fails, querying falls back to local lexical/symbol retrieval; explicit `--embedding-provider openai` errors instead of silently pretending semantic search ran. Version-1 indexes remain readable, but must be rebuilt to gain embeddings. Because embedding generation sends indexed source chunks to the selected provider, it is opt-in and defaults to `none`.

Generated indexes are written under `.code_memory/` and are ignored by git. This design deliberately keeps the evidence in a simple inspectable local artifact instead of requiring a vector database.

To explain a specific discovered entrypoint:

```bash
.venv311/Scripts/quant-agent.exe explain-entrypoint --repo C:/path/to/xgboost --file research/AAA_best_cadidate_correct_data/run_current_strategy_clean_ablation.py --provider deepseek
```

To generate a patch proposal without modifying the target repo:

```bash
.venv311/Scripts/quant-agent.exe suggest-fix --repo C:/path/to/xgboost --file research/AAA_best_cadidate_correct_data/run_current_strategy_clean_ablation.py --issue stale-metadata --promoted-variant-id base5_combo_accel2_mom12
```

Fix proposals are written under `.fix_proposals/`. When a matching `.code_memory/<repo>/code_memory.json` exists, `suggest-fix` attaches source-memory evidence to the proposal automatically. Proposal JSON now includes a structured review contract: problem, target files, intended behavior, patch plan, verification commands, evidence, memory context, and a quality gate.

For unfamiliar source patterns, generate a review-only LLM draft:

```bash
.venv311/Scripts/quant-agent.exe suggest-fix --repo C:/path/to/repo --file research/run_strategy.py --issue generalized-fix --problem "Describe the desired source change" --provider mock
```

`generalized-fix` proposals are not auto-applicable. They save `llm_draft.json`, `source_trace.md`, and optionally `candidate.patch` for review.

To verify and then apply a proposal:

```bash
.venv311/Scripts/quant-agent.exe apply-fix --proposal .fix_proposals/xgboost_stale_metadata_current_strategy_clean_ablation/proposal.json
.venv311/Scripts/quant-agent.exe apply-fix --proposal .fix_proposals/xgboost_stale_metadata_current_strategy_clean_ablation/proposal.json --yes
```

Without `--yes`, `apply-fix` performs a dry run and writes `apply_result.json`.

## Project Shape

```text
configs/                 Strategy configuration files.
.codex/                  Project-local Codex MCP configuration.
.agents/skills/          Codex workflow instructions over the MCP tools.
src/quant_agent/          Application package.
src/quant_agent/backtest/ Backtest adapter boundary.
src/quant_agent/audits/   Manual audit actions.
src/quant_agent/agent/    LangGraph agent-session loop.
src/quant_agent/mcp_server.py Thin stdio MCP adapter over the shared tools.
src/quant_agent/replay/   Trade-decision replay support.
src/quant_agent/models/   Structured data contracts.
runs/                     Generated experiment artifacts.
tests/                    Unit tests.
evals/                    Isolated end-to-end Agent scenarios and deterministic graders.
```

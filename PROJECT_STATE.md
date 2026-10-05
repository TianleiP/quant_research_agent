# Quant-Agent Project State

Last updated: 2026-09-19

This file is the human-readable project state index. It is not the only source of truth. The real operational records live in `runs/`, `.fix_proposals/`, `.discovery/`, `.source_traces/`, and `configs/`.

## Current Stage

`quant-agent` is a working quant backtest audit CLI plus a persisted LangGraph Agent with configurable SQLite/PostgreSQL checkpoints, a local MCP server, and a repository Codex Skill.

It can run or ingest a configured external backtest, normalize strategy artifacts, produce repeatable run folders with enriched metrics and reproducibility metadata, run deterministic audits, replay position-aware daily decisions, route natural-language requests to commands, maintain strategy lineage, close promotion warnings into a durable review report, build/query hybrid code memory, generate source traces, generate structured memory-informed fix proposals, create review-only generalized LLM fix drafts for unfamiliar source patterns, apply reviewed deterministic patch proposals, and run a persisted approval-gated patch-run-inspect lifecycle. The Agent supports native tool calling, a structured multi-step plan stored in State, an evaluator after each meaningful action, bounded replanning, and cited code retrieval that fuses semantic, lexical, symbol, and path evidence. The same 15 registered tools are now available to Codex over MCP, while a Skill teaches Codex the evidence-first workflow without moving safety policy into the prompt. A separate six-scenario end-to-end eval suite checks complete workflow behavior with isolated fixtures, `scripts/run_repair_demo.py` provides a public self-contained repair path, and GitHub Actions verifies the publication audit, PostgreSQL-backed test suite, and all six evals on pushes and pull requests.

It is not a general autonomous coding agent. Its scope is deliberately limited to registered quant research and repair tools:

```text
CLI command -> deterministic workflow -> optional one-shot LLM call -> artifacts -> stop
```

The most capable LangGraph path is now:

```text
task -> observe -> plan -> ReAct tool choice -> deterministic safety check
     -> execute or interrupt for approval -> evaluate -> continue/replan/finish
     -> SQLite/PostgreSQL checkpoint + JSONL audit log
```

The loop keeps rule, legacy JSON selector, and plain ReAct modes for comparison. In `plan-react`, the provider must first return a schema-validated ordered plan. That plan, its versions, current step, evaluator feedback, and replan count are stored in LangGraph State. The ReAct executor only sees tools allowed for the current step. The evaluator can continue, complete, block, or request a revised remaining plan, but its claims are checked against actual successful tool actions and both evaluations and replans have hard limits. Approval-gated work pauses through LangGraph `interrupt()` and resumes from the selected SQLite or PostgreSQL backend with `Command(resume=...)`; JSONL remains a human-readable audit trail. SQLite stays the zero-configuration default. PostgreSQL uses the official LangGraph saver through a thin persistence factory and has verified independent-process restart/resume coverage.

## Latest Verified Run

- Run id: `base5_combo_accel2_mom12_20260609_002`
- Run directory: `runs/base5_combo_accel2_mom12_20260609_002`
- Config: `configs/xgboost_live_strategy.yaml`
- External repo: machine-local path configured in `configs/xgboost_live_strategy.yaml`
- Entrypoint: `research/AAA_best_cadidate_correct_data/run_current_strategy_clean_ablation.py`
- Expected variant: `base5_combo_accel2_mom12`
- Fresh subprocess run completed: yes
- Elapsed time: about 11.6 minutes
- Latest local test status: `147 passed` with PostgreSQL integration enabled; `145 passed, 2 deselected` without the service

## Agent Runtime

The official quant-agent runtime is now Python 3.11:

```text
.venv311/  Python 3.11.1 official agent runtime
.venv/     Python 3.7.2 legacy local environment
```

The Python 3.11 venv was created with:

using the workstation's Python 3.11 interpreter.

The project is installed in editable mode in `.venv311`, and the full test suite passes there:

```text
.venv311/Scripts/python.exe -m pytest
147 passed with PostgreSQL integration enabled
145 passed, 2 deselected without the PostgreSQL service
```

The end-to-end Agent eval suite is separate from those tests:

```text
.venv311/Scripts/python.exe -m evals.run
6 passed, 0 failed
```

It covers read-only evidence gathering, rejected approval with byte-identical targets, a successful source repair with artifact verification, failed verification with one bounded replan, repeated-failure loop protection, and stdio MCP/direct-registry parity. Its semantic retrieval probe uses deterministic fake embeddings with a low-lexical-overlap query. Results are emitted as JSON and Markdown under `evals/results/<suite-id>/`.

The installed CLI wrapper also works:

```text
.venv311/Scripts/quant-agent.exe metrics --run latest
```

The first LangGraph command is:

```text
.venv311/Scripts/quant-agent.exe agent-session --task "summarize current project status"
```

Structured LLM planner smoke test:

```text
.venv311/Scripts/quant-agent.exe agent-session --task "inspect artifact and promotion status with metrics" --planner llm --provider mock
```

Structured plan + ReAct + evaluator/replanner smoke test:

```text
.venv311/Scripts/quant-agent.exe agent-session --task "inspect artifact and promotion status with metrics" --planner plan-react --provider mock --max-steps 5 --max-replans 2
```

Natural-language command routing:

```text
.venv311/Scripts/quant-agent.exe ask "show latest metrics"
.venv311/Scripts/quant-agent.exe ask "why did it trade on 2000-03-13"
.venv311/Scripts/quant-agent.exe ask "show latest metrics" --execute
```

Cost/slippage stress audit:

```text
.venv311/Scripts/quant-agent.exe audit costs --run latest
```

Strategy lineage ledger:

```text
.venv311/Scripts/quant-agent.exe lineage show
.venv311/Scripts/quant-agent.exe lineage update --strategy <strategy> --previous <baseline>
```

Promotion warning closure:

```text
.venv311/Scripts/quant-agent.exe close-promotion --run latest --diagnose --provider mock
```

Local code memory:

```text
.venv311/Scripts/quant-agent.exe memory build --repo C:/path/to/xgboost
.venv311/Scripts/quant-agent.exe memory query --index .code_memory/xgboost/code_memory.json --query "where are positions trades rankings exported"
```

Opt-in semantic hybrid index:

```text
.venv311/Scripts/quant-agent.exe memory build --repo C:/path/to/xgboost --embedding-provider openai --embedding-model text-embedding-3-small --embedding-dimensions 256
```

The local index now uses BM25, exact functions/classes and paths, optional dense embeddings, and weighted reciprocal-rank fusion. Every result carries its component scores and a `file:start-end` citation. Old indexes remain queryable. Semantic indexing is explicit because source chunks are sent to the selected embedding provider; without credentials the query path stays usable through local lexical/symbol fallback.

Paused sessions can be resumed with:

```text
.venv311/Scripts/quant-agent.exe agent-resume --session <session_id> --approve
.venv311/Scripts/quant-agent.exe agent-resume --session <session_id> --reject
```

Patch-run-inspect lifecycle:

```text
.venv311/Scripts/quant-agent.exe agent-session --task "complete latest proposal patch-run-inspect lifecycle" --max-steps 10
```

If the fresh verification run fails, or if artifact inspection is not satisfied, the lifecycle writes a non-applicable revision proposal under `.fix_proposals/` with `failure_context.md`. For supported missing position/trade/ranking export failures, it also synthesizes a revised missing-export proposal, with an auto-applicable patch only when the source matches a known safe pattern. When that synthesized proposal is applicable, the lifecycle now restarts from it and continues through the same approval-gated dry-run/apply/verify sequence. A revision loop guard blocks repeated synthesized proposal signatures or repeated failure signatures with a clear blocked status.

Keep external backtest runtimes separate from the agent runtime. The XGBoost research repo still uses its own Python 3.7 subprocess from `configs/xgboost_live_strategy.yaml`.

`pyproject.toml` now requires Python `>=3.11` and includes LangGraph as a project dependency for the agent-session loop.

Installed in `.venv311`:

```text
langgraph 1.2.4
```

The Agent loop is implemented with deterministic, legacy structured selector, native ReAct, and structured plan-ReAct modes, argument defaults, persisted execution, bounded evaluation/replanning, and CLI approval/resume gates.

## MCP and Codex Skill

The project now exposes the same central tool registry through an official MCP Python SDK stdio server:

```text
.venv311/Scripts/quant-agent-mcp.exe --root C:/path/to/quant-agent
```

The MCP adapter publishes 15 tools with JSON input schemas, structured output, and read-only/destructive/idempotent/open-world annotations derived from `ToolSpec`. Safe tools can run immediately. Approval-gated tools are disabled by default at the server boundary; a machine-local `.codex/config.toml`, copied from `.codex/config.example.toml`, can explicitly enable them and ask Codex to approve writes. Even after host approval, calls still execute through the existing argument resolver, deterministic risk validation, path checks, proposal checks, and shared handlers.

The repository Skill is `.agents/skills/quant-research-workflow/SKILL.md`. It describes the `inspect -> diagnose -> propose -> approve -> execute -> verify -> report` sequence and declares the `quant_agent` MCP dependency. This keeps responsibilities separate: the Skill teaches the workflow, MCP transports tool calls, and deterministic Python code enforces safety and performs the quant work.

Latest metrics:

```text
CAGR:         27.5%
Max drawdown: -34.7%
Calmar:        0.79
Turnover:     48.8 / year
Exposure:     72.5%
```

Latest position-level artifacts:

```text
positions.csv: 33,359 rows
trades.csv:    17,317 rows
rankings:      31,025 rows
```

Latest artifact contract:

```text
recommendation: satisfied
missing_position_level_artifacts: []
```

Latest promotion:

```text
recommendation: review_required
failures: []
```

Remaining warnings:

```text
source_current_variant_is_control
upstream_flags_present
validation_cagr_outperformance
cost_stress_review_required
robustness_review_required
```

Latest promotion closure:

```text
recommendation: needs_human_decision
closed: 1
needs_action: 0
needs_decision: 4
blocked: 0
```

Latest cost stress:

```text
recommendation: review_required
high-stress CAGR: 15.61%
high-stress max drawdown: -50.58%
```

## Implemented Capabilities

- Installable Python package through `pyproject.toml`
- CLI command wrapper: `.venv/Scripts/quant-agent.exe`
- Config-driven strategy loading from YAML
- Dummy backtest engine for scaffold testing
- Subprocess CSV adapter for external research backtests
- Run registry with per-run manifests, metrics, flags, reports, and copied source artifacts
- Artifact contract inspection
- Lag safety audit
- Robustness audit
- Validation attribution audit
- Cost/slippage stress audit
- Promotion audit
- Promotion warning closure report
- Strategy lineage tracker in `state/strategy_lineage.json`
- LLM diagnosis through mock/OpenAI/DeepSeek provider interface
- Repo discovery and entrypoint explanation
- Source tracing for missing position/trade/ranking exports
- Structured memory-informed fix proposal generation
- Review-only generalized LLM fix synthesis through `suggest-fix --issue generalized-fix`
- Reviewed patch application through `apply-fix`
- Approval-gated patch-run-inspect lifecycle in `agent-session`
- Verification-failure revision proposal generation
- Revision patch synthesis for supported missing-export artifact failures
- Proposal-aware lifecycle restart from synthesized revised proposals
- Revision loop guard for repeated synthesized proposals and repeated failure signatures
- Agent argument planning from project state, latest run metadata/audits, and latest proposal artifacts
- Position-aware replay using `positions.csv`, `trades.csv`, and rankings artifacts
- Natural-language command router through `quant-agent ask`
- Hybrid code memory using optional OpenAI embeddings, BM25, exact symbol/path matching, and weighted reciprocal-rank fusion
- Cited code evidence with per-channel retrieval scores and backward-compatible version-1 index reading
- Safe agent action `query_code_memory` before broad source/fix planning
- Proposal quality gate with required problem, target file, intended behavior, patch plan, verification, evidence, and memory context fields
- Enriched saved metrics with annualized volatility, Sharpe, Sortino, period returns, drawdown metadata, trade count, and exposure summaries
- Run manifest reproducibility block with runtime, config, adapter, source, data, cost, and artifact row-count context
- Shared JSON-schema tool catalog reused by CLI planning and provider tool calling
- Native function/tool-calling ReAct executor with correlated tool results
- Configurable LangGraph SQLite/PostgreSQL checkpoint persistence with native interrupt/resume
- Cross-process backend-parity tests for pending interrupts, reject-without-execution, and approve-exactly-once behavior
- Structured multi-step plan persisted in Agent State
- Evidence-checked evaluator and bounded remaining-plan replanner
- Deterministic risk and approval enforcement after every model tool choice
- Official MCP stdio server exposing all 15 shared tools with structured results and behavioral annotations
- Server-side gated-tool opt-in plus Codex host approval for write operations
- Repository Codex Skill for the evidence-first repair and verification workflow

## External XGBoost Integration

The current XGBoost integration uses a subprocess adapter. Quant-agent runs the research script, then ingests known CSV outputs.

The XGBoost script has been patched to export:

```text
positions/base5_combo_accel2_mom12.csv
trades/base5_combo_accel2_mom12.csv
rankings/base5_combo_accel2_mom12.csv
```

The detailed exports are now guarded so only the promoted live variant is exported:

```python
if spec.variant_id == PROMOTED_LIVE_VARIANT_ID:
```

This preserves all daily curves and summary metrics while avoiding expensive detailed exports for every variant.

Important caveat: the XGBoost output folders are not cleaned by the research script. Old non-promoted detailed files may remain there as stale leftovers. Quant-agent should trust only configured expected files and fresh run metadata.

## Persistent State Locations

Human-readable implementation state:

```text
PROJECT_STATE.md
```

Machine-readable implementation state:

```text
state/project_state.json
state/capabilities.json
state/strategy_lineage.json
```

Operational run state:

```text
runs/<run_id>/manifest.json
runs/<run_id>/engine_metadata.json
runs/<run_id>/metrics.json
runs/<run_id>/flags.json
runs/<run_id>/audits/*.json
runs/<run_id>/audits/promotion_closure.json
```

Patch proposal/application state:

```text
.fix_proposals/*/proposal.json
.fix_proposals/*/proposal.patch
.fix_proposals/*/apply_result.json
```

Discovery and trace state:

```text
.discovery/*
.source_traces/*
```

Configuration state:

```text
configs/*.yaml
```

Conceptual product goal:

```text
goal.md
```

## Known Gaps

- Semantic retrieval currently uses an external OpenAI embedding provider; no local neural embedding backend is packaged
- Subprocess CSV adapter still requires explicit config for each external backtest shape
- Standalone `agent-session` approval remains CLI-based; the Codex MCP route uses native host write-approval prompts
- State refresh updates `state/project_state.json`; `PROJECT_STATE.md` is still maintained manually
- Revision synthesis currently supports missing position/trade/ranking export failures only; other verification failures remain manual revision plans
- Cross-provider eval coverage is intentionally small: the default suite is deterministic and mock-backed, with a real-provider smoke path kept optional

## Next Recommended Step

Next, turn the completed architecture, public demo, and CI evidence into a
concise recorded recruiter-facing walkthrough:

```text
architecture overview -> run one read-only scenario -> reject one gated action
-> show successful repair evidence and loop guard -> show MCP parity
-> link the JSON/Markdown eval report
```

Keep the walkthrough small and evidence-focused; the scenario coverage,
self-contained repair demo, MIT license, public GitHub repository, and CI
workflow are already implemented. A tagged portfolio release and a narrowly
scoped real-provider smoke sample are optional follow-ups. The separate
promotion report still has four business-risk warnings requiring human
decisions; those are strategy-governance decisions rather than missing Agent
architecture.

## Maintenance Rule

Update this file and `state/project_state.json` after any meaningful project milestone:

- new capability implemented
- external repo integration changed
- latest verified run changes
- important audit result changes
- major known gap closed
- next recommended step changes

Do not copy full audit payloads here. Store summaries and pointers only.

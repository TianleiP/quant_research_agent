---
name: quant-research-workflow
description: Inspect, diagnose, propose, approve, execute, verify, and report changes in this quant-agent project through its MCP tools. Use for run health, metrics, artifact gaps, source tracing, fix proposals, reviewed patch application, fresh verification, and project-state reporting. Do not use for unrelated repositories or unsupported strategy redesign without evidence.
---

# Quant Research Workflow

Use the `quant_agent` MCP server as the action layer. Follow explicit user instructions when they narrow or change this workflow.

## Workflow

1. Read `read_project_state` and `read_capabilities` before making broad claims about the project.
2. Inspect the latest evidence with the smallest relevant combination of `inspect_latest_run`, `metrics_latest`, `inspect_artifacts_latest`, and `promote_latest`.
3. When the source location is uncertain, use `query_code_memory` before proposing a trace or fix. Cite returned file and line evidence.
4. Use `trace_source` to produce a source trace and `suggest_fix` to create a reviewable proposal. Treat both as approval-gated operations.
5. Use `inspect_proposal` and explain the target, intended behavior, patch plan, evidence, and verification commands before applying anything.
6. Obtain separate, explicit user approval before each gated transition: dry-run, patch application, and fresh external run. Never reuse one approval for another operation.
7. After an approved patch, verify with the proposal's checks and then inspect the latest run, artifacts, metrics, and promotion evidence. If verification fails, use `suggest_revision` and, only for supported failure shapes, `synthesize_revision`.
8. Report what evidence was inspected, what changed, which approvals were used, the verification result, and any unresolved risk. Never report success without tool evidence.

The deterministic tool output is authoritative when it conflicts with model inference. Do not bypass the tool registry, approval gates, path validation, or proposal quality checks.

Read [references/tool-guide.md](references/tool-guide.md) when mapping a request to tools or deciding whether an action needs approval.

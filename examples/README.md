# Self-contained repair demo

This demo shows the public project without requiring the author's private
strategy repository or an API key. It creates an isolated copy of a small
external strategy fixture, produces an initial run with missing position-level
exports, and sends the real Quant-Agent through:

```text
inspect -> retrieve -> trace -> propose -> approve -> apply -> verify
```

From the repository root, run:

```powershell
.venv311\Scripts\python.exe scripts\run_repair_demo.py --approve-demo-actions
```

`--approve-demo-actions` is deliberately explicit. The demo still reaches a
real LangGraph `interrupt()` before each gated action and resumes from its
SQLite checkpoint. The demo controller approves only the expected trace,
proposal, dry-run, patch, and fresh-run actions; it rejects anything else.

Every invocation uses a new ignored directory under `.demo_runs/`. The source
fixture is copied before it is patched, so the checked-in example and any real
strategy repository remain unchanged. Open `demo_report.md` in the printed
workspace to see the tool sequence, approval evidence, and deterministic
success checks.

The included `demo_backtest.py` is a fast verification harness, not a trading
model. It stands in for an expensive external backtest and emits the full
artifact contract only after the expected source repair is present.

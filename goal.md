# Quant Research Agent Outline

## 1. Project Goal

This project aims to build a domain-specific quantitative research agent that helps manage, audit, diagnose, and document systematic trading-strategy experiments.

The goal is not to replace general-purpose coding agents such as Codex CLI or Claude Code. Instead, this agent focuses on quant-specific workflows that are usually inconvenient for general coding agents to handle repeatedly, such as lag-safety validation, backtest reproducibility, parameter-neighborhood robustness, strategy promotion audits, and trade-decision replay.

The agent acts as a lightweight research workstation for strategy development rather than a simple LLM coding wrapper.

---

## 2. Core Design Principle

The agent should not produce a long full audit report every time it runs.

Instead, it separates functionality into two layers:

1. **Default run behavior**
   Fast, concise, low-cost checks that happen automatically after each experiment.

2. **Manual UI actions**
   Heavier, more specialized tools triggered by the user through a terminal UI, slash command, or CLI subcommand.

This keeps daily research fast while still making deeper audits available when needed.

---

## 3. Default Features

These features are executed automatically after a normal strategy run.

### 3.1 Backtest Execution

Run a strategy using a specified configuration file.

Example:

```bash
quant-agent run --config configs/live_strategy.yaml
```

The agent runs the backtest and saves all relevant artifacts under a unique run ID.

---

### 3.2 Experiment Registry

Every run is automatically recorded with metadata required for reproducibility.

Saved metadata may include:

* Run ID
* Strategy name
* Config hash
* Git commit hash
* Data version
* Universe version
* Backtest start and end dates
* Rebalance rule
* Transaction-cost assumptions
* Slippage assumptions
* Generated metrics
* Output artifact paths

Example run folder:

```text
runs/
  20260602_001/
    config.yaml
    manifest.json
    metrics.csv
    trades.csv
    positions.csv
    short_report.md
```

This prevents strategy research from becoming a collection of undocumented one-off experiments.

---

### 3.3 Core Metrics Summary

After each run, the agent displays a concise metrics summary.

Example output:

```text
Latest run: top8_partial20_20260602_001

CAGR: 36.2%
Max Drawdown: -34.8%
Calmar: 1.04
Turnover: 18.5 / year
Exposure: 92.4%
```

The default output should stay short and readable.

---

### 3.4 Basic Risk Flags

The agent performs lightweight automatic checks and only surfaces important warnings.

Example:

```text
Flags:
- WARN: Top2 sleeve contribution appears highly concentrated.
- WARN: Liquidity assumption requires confirmation.
- PASS: No obvious unshifted rolling signal pattern detected.
```

This is not a full audit. It is a quick safety scan designed to catch obvious problems.

---

### 3.5 Short Report Generation

Each normal run generates a short Markdown report containing:

* Core metrics
* Basic charts or summary tables
* Key warnings
* Paths to saved artifacts
* Suggested next actions

Example:

```text
short_report.md
```

The short report is meant for daily use, not promotion-level documentation.

---

## 4. Manual UI Features

The following features are triggered manually through a terminal interface or command system.

Example terminal UI:

```text
Latest run: top8_partial20_20260602_001

[1] View metrics
[2] Run lag-safety audit
[3] Run robustness sweep
[4] Replay a date
[5] Diagnose anomaly
[6] Check docs consistency
[7] Generate promotion report
```

---

### 4.1 View Metrics

Manual UI option:

```text
[1] View metrics
```

Purpose:

Display detailed performance statistics for a selected run.

Possible output includes:

* CAGR
* Annualized volatility
* Max drawdown
* Calmar ratio
* Sharpe ratio
* Sortino ratio
* Year-by-year returns
* Monthly returns
* Turnover
* Exposure
* Number of trades
* Best and worst periods
* Drawdown recovery time

This function is manual because the full metrics table is often too verbose for every normal run.

---

### 4.2 Lag-Safety Audit

Manual UI option:

```text
[2] Run lag-safety audit
```

Purpose:

Check whether the strategy uses only information that would have been available at the time of trading.

The audit should inspect risks such as:

* Same-day close used for same-day execution
* Unshifted rolling indicators
* Unshifted ranking signals
* Liquidity filters using unavailable volume data
* Market gates using information from the execution day
* Strategy crash brakes reacting to same-day portfolio returns
* Universe membership leakage
* Current constituent lists used for historical backtests

Example output:

```text
Lag-Safety Audit

PASS: Momentum ranks are shifted by one session.
WARN: Liquidity filter depends on volume availability assumption.
FAIL: Crash brake references strategy_return[t] before execution.
UNKNOWN: Universe membership source is not versioned.
```

This is one of the most important domain-specific features of the agent.

---

### 4.3 Robustness Sweep

Manual UI option:

```text
[3] Run robustness sweep
```

Purpose:

Test whether a strategy performs well across nearby parameter settings rather than only at a single optimized point.

The agent may test variations such as:

* Top-N selection size
* Top2 sleeve weight
* Top8 / Top10 mix ratio
* Momentum lookback windows
* Liquidity threshold
* MACD haircut parameters
* Market-risk gate thresholds
* Crash-brake parameters
* Rebalance frequency

Example output:

```text
Robustness Summary

Core variant: top8_partial20
Neighbor stability: Strong
Fragile dimensions: Top2 sleeve weight, MACD haircut threshold
Danger sign: Performance peak is narrow around one parameter combination.
```

This helps detect overfitting and isolated best-result artifacts.

---

### 4.4 Trade Decision Replay

Manual UI option:

```text
[4] Replay a date
```

Purpose:

Explain exactly why the strategy made a specific decision on a specific date.

Example command:

```bash
quant-agent replay --run latest --date 2021-03-08
```

Example output:

```text
Date: 2021-03-08

Information available before decision:
- Prices through 2021-03-05 close
- Momentum ranks through 2021-03-05
- Market gate: risk-on
- MACD haircut: active
- Liquidity-eligible universe: 431 stocks

Selected sleeve:
- 20% Top2: AAPL, NVDA
- 80% Top8: AAPL, NVDA, MSFT, AMD, ...

Reason:
- AAPL ranked #1 by momentum score.
- NVDA ranked #2 by momentum score.
- Market filter allowed equity exposure.
- Partial pocket rule applied because MACD haircut was active.

Execution:
- Signals generated after prior close.
- Orders executed on next session.
```

This function is useful for debugging, explaining behavior, and verifying that strategy actions are causally valid.

---

### 4.5 Diagnose Anomaly

Manual UI option:

```text
[5] Diagnose anomaly
```

Purpose:

Investigate unusual performance behavior.

The agent can flag and explain issues such as:

* One year performing much worse than expected
* An unusually fast drawdown recovery
* Identical drawdowns across many variants
* Performance driven by only a few stocks
* Extremely high CAGR with suspiciously low turnover
* Sudden strategy failure after a market regime change
* Sharp difference between training and test periods
* A variant outperforming all neighbors by too much

Example output:

```text
Anomaly Diagnosis

Finding 1:
2021 return is unusually low relative to the full-period CAGR.

Possible explanation:
The strategy was underexposed during several strong market periods due to the market-risk gate and MACD haircut.

Recommended checks:
- Replay low-return months.
- Compare exposure against SPY and QQQ.
- Check whether Top2 sleeve was repeatedly suppressed.
```

This feature combines deterministic statistics with LLM-based explanation.

---

### 4.6 Documentation Consistency Check

Manual UI option:

```text
[6] Check docs consistency
```

Purpose:

Check whether strategy documents, configs, reports, and code agree with each other.

The agent should detect problems such as:

* README says one strategy is live, but config uses another
* Historical documents describe obsolete baselines as if they are current
* Strategy names are inconsistent across files
* Promotion reports conflict with the actual run ID
* Compatibility labels are confused with true strategy names
* Deprecated parameter settings remain in current documentation
* Mechanism documents omit important current rules

Example output:

```text
Documentation Consistency Audit

PASS: live_strategy.yaml uses top8_partial20.
WARN: BASELINE_MECHANISMS.md contains historical baseline descriptions.
FAIL: README still describes top10_partial25 as the current live baseline.
SUGGEST: Treat fav_top2_h1 as a compatibility label, not the current strategy name.
```

This is useful because quant research often accumulates outdated reports and confusing variant names.

---

### 4.7 Generate Promotion Report

Manual UI option:

```text
[7] Generate promotion report
```

Purpose:

Run a full pre-promotion audit before marking a strategy as the current live baseline.

This is heavier than a normal run and may include:

* Full lag-safety audit
* Robustness sweep
* Transaction-cost stress test
* Slippage sensitivity test
* Documentation consistency check
* Reproducibility manifest freeze
* Strategy mechanism summary
* Comparison against previous live baseline
* Known weaknesses and unresolved risks
* Final promotion recommendation

Example command:

```bash
quant-agent promote --run latest --name top8_partial20
```

Example output artifact:

```text
PROMOTION_AUDIT_top8_partial20.md
```

This feature is intended for major strategy changes, not daily research.

---

## 5. Optional Advanced Manual Features

These can be added after the MVP.

### 5.1 Bias and Realism Test Suite

Manual trigger:

```bash
quant-agent test-bias --run latest
```

Possible tests:

* Extra-lag test
* Transaction-cost stress test
* Slippage stress test
* Randomized rebalance date test
* Remove top-N winners test
* Liquidity capacity test
* Walk-forward split test
* Missing-data injection test

Purpose:

Detect whether performance depends on unrealistic assumptions or fragile timing.

---

### 5.2 Known-at-Time Data Ledger

Manual or configuration-based feature.

The agent maintains a structured record of when each data field becomes available.

Example:

```yaml
close:
  available: same_day_close
  usable_for: next_session

volume:
  available: same_day_close
  usable_for: next_session

sp500_membership:
  available: historical_asof
  usable_for: next_session

earnings:
  available: timestamped_release
  usable_for: after_release_only
```

Purpose:

Make lag-safety auditing more deterministic and less dependent on LLM judgment.

---

### 5.3 Strategy Lineage Tracker

Manual or default registry feature.

The agent tracks strategy evolution over time.

Example:

```text
old_baseline
  -> q60_handoff
  -> fav_top2_h1
  -> top10_partial25
  -> top8_partial20
```

For each promoted version, the agent records:

* Previous baseline
* Reason for promotion
* Changed rules
* Inherited rules
* Removed rules
* Known weaknesses
* Relevant audit files
* Final promotion date

Purpose:

Prevent confusion between historical variants, compatibility labels, and the current live strategy.

---

## 6. Suggested MVP Scope

The first useful version should include:

### Default MVP Features

* Run backtest from config
* Save run manifest
* Save metrics, trades, and positions
* Show concise metrics summary
* Run basic warning checks
* Generate short report

### Manual MVP Features

* View detailed metrics
* Run lag-safety audit
* Run robustness sweep
* Replay a selected date
* Diagnose anomaly
* Check documentation consistency
* Generate promotion report

This MVP is already meaningfully different from a general coding agent because it provides a structured quant research workflow rather than generic code editing.

---

## 7. Example User Flow

### Daily research run

```bash
quant-agent run --config configs/live_strategy.yaml
```

Output:

```text
Run saved: top8_partial20_20260602_001

CAGR: 36.2% | MaxDD: -34.8% | Calmar: 1.04

Flags:
- WARN: Top2 sleeve contribution is concentrated.
- PASS: No obvious unshifted rolling signal detected.

Next actions:
[1] View metrics
[2] Run lag-safety audit
[3] Run robustness sweep
[4] Replay a date
[5] Diagnose anomaly
[6] Check docs consistency
[7] Generate promotion report
```

### Promotion workflow

```bash
quant-agent promote --run top8_partial20_20260602_001
```

The agent then performs a full audit and generates a promotion report.

---

## 8. Positioning

This agent is best described as:

```text
A domain-specific quant research audit and experiment-management agent.
```

Its core advantage over general coding agents is not better code generation. Its advantage is that it embeds quant-specific workflows:

* Time-causality checking
* Backtest reproducibility
* Strategy-version tracking
* Parameter-neighborhood robustness
* Trade-decision replay
* Promotion-level audit generation
* Documentation consistency checking

This makes the project more valuable than a simple GPT wrapper or a simplified coding assistant.

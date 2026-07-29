# Trading Bot — Broker-Agnostic Core

## What this is

A resilient, unattended trading bot whose decision logic (agent-based
composite scoring, risk rules) is fully decoupled from any specific broker,
so the broker can change without touching the decision logic. Built to
extend the existing SPX bull-put-spread composite-scoring pattern
(Almanac + Macro + Technical + LLM synthesis + Human Score) to also gate
NQ/ES futures entries/exits, plus a new Fundamental Agent.

Every material build decision — including rejected alternatives — is logged
in `docs/ARCHITECTURE_DECISIONS.md`. That file is the source of truth for
"why did we build it this way," and feeds directly into the book chapter and
any research-paper methodology writeup.

## Structure

```
agents/
  base.py              Agent interface + AgentOutput schema
  composite.py          Weighted composite scorer -> Bias (long/short/stand-aside)
  fundamental/agent.py   NEW — FRED macro data + earnings tone (this session's build)
  almanac/, macro/, technical/   Stubs — to be merged from the existing
                                  market-prediction-engine repo, not
                                  rebuilt from scratch
execution/
  base.py               ExecutionAdapter interface — the broker swap boundary
  ibkr_adapter.py        First implementation, against IBKR Client Portal API
config.py               Single place broker selection happens
docs/
  ARCHITECTURE_DECISIONS.md   Running ADR log
```

## Cross-repo dependency — not yet resolved, stated plainly

This code lives in `ibkr-options-income` (under `bot_core/`), but
`agents/adapters/score_mapping.py` imports from `agents.schemas` and calls
the real Almanac/Macro/Technical/LLM-synthesis agents, which physically live
in the separate `market-prediction-engine` repo. As pushed here, this code
will NOT run standalone — the dependency is real and unresolved, not hidden.
Options to close this gap, none chosen yet: a git submodule pointing at
market-prediction-engine, a CI step that checks out both repos before running
the pipeline, or packaging market-prediction-engine's agents as an installable
module. Whichever is chosen, the goal from ADR-005 stands: never fork/copy the
agent code itself — only ever reference it.

## Status — what's real vs. stubbed

- **Real, unmodified, pulled from `ProfDrTan/market-prediction-engine`:**
  `agents/schemas.py`, `agents/almanac/almanac_agent.py`,
  `agents/macro/macro_agent.py`, `agents/technical/technical_agent.py`,
  `agents/llm/multi_model_runner.py`, `calibration/trust_weights.py`. Not one
  line changed from the course-project versions.
- **Real and tested this session:** `agents/adapters/score_mapping.py`
  (translates the real agents' outputs into the composite scorer's
  normalized format — verified against constructed realistic data, see
  ADR-005 for the full test), `agents/composite.py`'s weighting/veto logic,
  `agents/fundamental/agent.py`'s FRED macro pull.
- **Verified working end-to-end (offline test):** almanac + macro + technical
  + LLM-synthesis outputs → score mapping → composite scorer → long/short/
  stand-aside bias. Live network calls (yfinance) couldn't be tested from
  this sandbox — its egress allowlist doesn't include Yahoo Finance — so
  this needs a real run in an environment with open network access (e.g.
  the actual GitHub Actions runner) before being called fully proven.
- **Stubbed, needs work before live:** IBKRAdapter's order-confirmation
  round-trip (IBKR often requires answering a confirmation message before an
  order actually submits — not yet handled, flagged in the adapter file).
- **Not started:** symbol-to-conid resolution for IBKR quotes, backtesting
  the score-mapping thresholds and composite weights against historical
  outcomes (currently first-pass judgment calls, not calibrated), Tiger/
  Moomoo adapters (intentionally deferred per ADR-002).

## Honest gaps to close next

1. Run this in an environment with real network access to confirm the live
   yfinance + FRED calls actually work end-to-end, not just the offline
   logic test done this session.
2. Handle IBKR's order-confirmation round trip — an unattended bot that
   silently stalls on an unanswered confirmation is a resilience failure,
   not a minor gap.
3. Backtest the score-mapping thresholds and composite weights before
   sizing real trades on them — every threshold right now is a first-pass
   judgment call, documented as such in ADR-005.
4. Confirm whether Tiger Brokers' auth flow is genuinely zero-touch (paper
   account test) before deciding whether to build that adapter.
5. NQ/ES futures signals currently proxy through `^NDX`/`^GSPC` cash-index
   tickers for the Almanac/Technical agents — a reasonable approximation,
   not an exact match to the futures contract's actual behavior (ADR-005).

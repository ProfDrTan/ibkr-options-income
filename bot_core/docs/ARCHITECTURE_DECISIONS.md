# Architecture Decision Log

This file is the running record of every material build decision, including the
rejected paths. It exists so nobody — including future-us — has to reconstruct
"why did we do it this way" from memory. Every entry is dated, states the
alternatives actually considered, and states the trade-off honestly, including
uncertainty where it exists. This file is the primary source for the "Broker
API Shootout" chapter in *The AI Trading Lab*, and for any research-paper
methodology section drawn from this project.

Format per entry: Decision / Alternatives considered / Why / Open uncertainty.

---

## ADR-001: Broker execution layer — adapter pattern, not a single hard-coded broker

**Decision:** All order placement, position queries, and market data pulls go
through a single abstract `ExecutionAdapter` interface (`execution/base.py`).
No agent, scoring, or risk logic anywhere in the codebase calls a broker SDK
or REST endpoint directly.

**Alternatives considered:** Hard-code against IBKR's Client Portal API
directly throughout the codebase (faster to write today, expensive to change
later).

**Why:** The broker comparison below (ADR-002) surfaced real uncertainty about
which platform wins long-term on the "resilient + zero recurring fees"
objective. IBKR is the correct starting choice today, but Tiger Brokers'
RSA-signed request model is a legitimate architectural alternative that may
prove superior for unattended operation. An adapter boundary means that
question stays open without blocking progress, and switching later is a new
adapter file, not a rewrite.

**Open uncertainty:** Whether Tiger Brokers' auth flow has *any* human-in-the-
loop step beyond the documented 30-day token refresh is not yet confirmed —
flagged for a live paper-account test before it's treated as settled.

---

## ADR-002: Platform comparison — why IBKR, not Tradier / Schwab / tastytrade / IG / Moomoo / Tiger (yet)

| Platform | Rejected because | Confidence |
|---|---|---|
| Tradier | Options-only via clean REST; futures routed through a separate Tradier Futures entity via CQG/Rithmic, plus a $10-35/mo subscription for commission-free options | High |
| IG | UK/international CFD broker; trades CFDs on the underlying, not the actual CME-listed futures contract; different regulatory treatment | High |
| Schwab | Free API, but futures support in the Trader API reads as market-data access — order placement for NQ/ES was not confirmed in available documentation | **Unconfirmed — needs a direct question to Schwab dev support** |
| tastytrade | Covers equities/options/futures/crypto in one API; rate limits are tighter (~60 req/min in third-party benchmarking); exact API fee structure not independently confirmed | Medium — worth a second look |
| Moomoo | Free API, confirmed CME futures support, but requires a persistent local gateway process (OpenD) — same architectural friction class as IBKR | High |
| Tiger Brokers | Free API, confirmed futures support, RSA key-signed requests (no persistent gateway, no push-notification 2FA per request) — **genuinely competitive, possibly superior to IBKR for unattended operation** | Medium-high — untested in practice |
| **IBKR (chosen)** | Free API, confirmed native futures + options execution in one account, already funded and live | — |

**Why IBKR now despite the 2FA friction:** it is the only platform with
*confirmed* free execution of both asset classes today, and the account is
already funded. The known cost is operational: a headless Client Portal
Gateway (via IBeam) plus an estimated (not guaranteed) weekly manual 2FA
touch, based on one practitioner account rather than an IBKR spec sheet.

**Revisit trigger:** if a live paper-account test on Tiger Brokers confirms
zero human-in-the-loop auth, re-evaluate migrating the adapter — this is
exactly the kind of decision the adapter pattern in ADR-001 is designed to
make cheap.

---

## ADR-003: Fundamental Agent scope

**Decision:** Fundamental Agent covers two inputs — (1) hard macro data
(Fed funds rate, CPI/PCE, GDP, NFP, 10Y-2Y spread) pulled from FRED's free
API, and (2) an earnings-season aggregate tone score produced by the existing
LLM synthesis layer summarizing index-level EPS/guidance commentary, rather
than a paid earnings-data feed.

**Why:** Keeps the "no new recurring fees" objective intact — FRED has no
paid tier, and reusing the existing multi-LLM synthesis step for earnings
tone avoids a second data subscription.

**Open uncertainty:** Earnings-tone-via-LLM-synthesis is a proxy for a real
aggregate EPS number, not the number itself — worth stating plainly in the
book rather than implying it's equivalent to a licensed earnings feed.

---

## ADR-004: Weekly bias / faster technical trigger split

**Decision:** Almanac + Macro + Fundamental agents run on a weekly cadence
and set a directional bias filter (long / short / stand-aside). The Technical
Agent runs more frequently and times the actual entry/exit trigger within
that weekly bias.

**Why:** Mirrors the cadence already proven in the SPX bull-put-spread bot
(weekly composite gate + specific strike/premium mechanics), extended to also
gate futures direction. Avoids re-running slow-moving macro/fundamental
scoring on every technical check.

---

## ADR-005: Real agents integrated via a translation layer, not a rewrite

**Decision:** The actual Almanac/Macro/Technical/LLM-synthesis agents were
pulled unmodified from `ProfDrTan/market-prediction-engine` (agents/schemas.py,
agents/almanac/almanac_agent.py, agents/macro/macro_agent.py,
agents/technical/technical_agent.py, agents/llm/multi_model_runner.py, plus
calibration/trust_weights.py which the LLM runner depends on). A new file,
`agents/adapters/score_mapping.py`, translates each agent's real typed output
(AlmanacOutput, MacroOutput, TechnicalOutput, SynthesisOutput) into the
normalized AgentOutput(score, confidence) the CompositeScorer consumes.

**Alternatives considered:** Rewrite the agents to natively return a
normalized score, so there's one schema everywhere.

**Why the translation layer instead:** the real agents are tested, working,
and already in production for the CP3405 course exercise. Rewriting them to
serve a second consumer (the trading bot) would mean maintaining two
diverging copies of logic that's supposed to be the same thing, and any bug
fix to the course version would need manually re-applying to the trading-bot
fork. A one-way adapter means the course code stays exactly as it is; if the
mapping thresholds prove wrong, only `score_mapping.py` changes.

**Open uncertainty — flagged plainly, not glossed over:** every threshold in
`score_mapping.py` (e.g., what RSI level dampens a trend score, how much
VIX distance should scale a macro regime call) is a first-pass judgment
call, not backtested against actual outcomes. This is the single biggest
gap between "architecturally sound" and "provably profitable" — the next
real step is backtesting these mappings against historical data before
sizing any real trade on them.

**Second open item:** the real Technical/Almanac agents run per-ticker
(built for SPX, NDX, IWM — the course's tracked indices). NQ and ES futures
track the Nasdaq-100 and S&P 500 respectively, so for *signal* purposes
(not execution) the plan is to run these agents against `^NDX`/`^GSPC` as
proxy tickers. This is a reasonable approximation, not an exact match —
futures carry basis/roll effects the cash index doesn't — and should be
named as an approximation in the book, not presented as identical.

## ADR-006: What today's paper-trading dry run actually broke, and the fixes

**Context:** first real end-to-end attempt to place a trade through this
architecture, on IBKR paper (2026-07-29). Three separate failures surfaced,
none of which were visible until actually trying it — exactly why this dry
run happened before any live capital was involved.

**Failure 1 — account ambiguity.** The IBKR connector used for tool calls
turned out to be bound to the live account (U10500387) with no way to select
paper from inside the chat session. Checking a browser screenshot for the
account ID told us the *browser* was on paper — it told us nothing about
which account the *connector* was pointed at. These are two independent
credentials. Confirmed via Anthropic's own documentation that connectors
currently support one bound account at a time, with multi-account support
requested but not yet released — this isn't a configuration mistake to fix,
it's a current platform limit. **Fix:** disconnect/reconnect the connector
itself with paper credentials when paper access is needed; treat "which
account is the connector bound to" as a check to run before every trade,
not an assumption.

**Failure 2 — duplicate order.** Two near-identical order instructions ended
up live simultaneously for the same spread, discovered only by looking
directly at the IBKR Orders screen. Nothing in the flow up to that point
checked for an existing open order before creating a new one. **Fix:**
`ExecutionAdapter.get_open_orders()` added, with a same-contract/same-side
duplicate check now run inside `place_order()` before any submission — see
`execution/ibkr_adapter.py`. This is a safety net, not a mathematically
complete duplicate-detector; it catches the exact failure mode observed.

**Failure 3 — missing calendar check.** An SPX bull put spread recommendation
was generated using a generic "SPX market today" news search, which missed
that FOMC's rate decision, and four separate mega-cap earnings reports, all
landed inside the trade's 2-day holding window. The Fundamental Agent
(ADR-003) was designed to catch exactly this class of event and didn't,
because it was never actually wired into the live recommendation flow that
day — a designed safeguard that isn't connected yet provides zero protection.
**Fix (still open, not yet built):** the Fundamental Agent's macro-calendar
check needs to run as a hard gate before any trade recommendation is
surfaced, not as a component that exists in the codebase but isn't called.
Tracked as the top priority for the next build session.

**Pattern to apply everywhere in this project, including future sessions:**
none of these three were caught by design review — all three were caught by
actually trying to execute a trade and watching it go wrong. That's the
argument for keeping paper-trading dry runs in the loop even after the
architecture "looks" complete on paper (pun noted).

*(New entries append below this line as the build progresses.)*

"""
Fundamental Agent — see docs/ARCHITECTURE_DECISIONS.md ADR-003 for scope
rationale (hard macro data + LLM-synthesized earnings tone, deliberately NOT
a paid earnings-data subscription).

Two sub-signals, averaged into one AgentOutput:
  1. Macro data signal — pulled from FRED (free, no API cost). Itself has
     two components, averaged together (see _score_macro):
       a. Yield curve slope (10Y-2Y) — recession/regime signal.
       b. 10-Year yield level/direction — discount-rate signal. Long-duration
          growth/tech names (NDX-heavy) are disproportionately sensitive to
          this: rising 10Y yield raises the discount rate applied to future
          earnings, compressing growth valuations more than value/short-
          duration names. This is a DIFFERENT channel from the curve-slope
          regime signal above and was missing until 2026-07-31 — the curve
          slope alone was silent on rate-level moves like the one that
          happened this week (10Y +2.93% w/w per the Macro agent's separate
          reading), which is exactly the kind of thing this sub-signal
          should have caught.
  2. Earnings tone signal — produced by the existing multi-LLM synthesis
     step during active earnings-season weeks; this is a PROXY for real
     aggregate EPS data, not a substitute for it, and the book/paper writeup
     should say so plainly rather than implying equivalence.

FRED series IDs used below (verify these are still current at
https://fred.stlouisfed.org/ before relying on them — series can be
discontinued or superseded):
  FEDFUNDS  -> Federal funds effective rate
  CPIAUCSL  -> CPI, all urban consumers
  PCEPI     -> PCE price index
  GDP       -> Gross Domestic Product
  PAYEMS    -> Nonfarm payrolls
  T10Y2Y    -> 10-Year minus 2-Year Treasury yield spread
  DGS10     -> 10-Year Treasury Constant Maturity Rate (added 2026-07-31 for
               the discount-rate/tech-sensitivity signal, distinct from
               T10Y2Y's curve-shape regime signal)
"""

import os
import requests
from typing import Optional
from agents.base import Agent, AgentOutput

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi": "CPIAUCSL",
    "pce": "PCEPI",
    "gdp": "GDP",
    "nonfarm_payrolls": "PAYEMS",
    "yield_curve_10y2y": "T10Y2Y",
    "treasury_10y": "DGS10",
}


class FundamentalAgent(Agent):
    name = "fundamental_agent"

    def __init__(self, fred_api_key: Optional[str] = None):
        # Free to obtain at https://fred.stlouisfed.org/docs/api/api_key.html
        self.fred_api_key = fred_api_key or os.environ.get("FRED_API_KEY")

    def run(self, context: dict) -> AgentOutput:
        if not self.fred_api_key:
            return AgentOutput(
                agent_name=self.name,
                score=0.0,
                confidence=0.0,
                rationale="FRED_API_KEY not configured — agent skipped, "
                          "not defaulted to a bullish/bearish guess.",
                stale=True,
            )

        macro_data = self._pull_macro_series()
        macro_score, macro_rationale = self._score_macro(macro_data)

        earnings_score, earnings_rationale = self._earnings_tone(context)

        # earnings_score is None outside earnings season by design — averaging
        # None into a float crashed the very first live run of this agent.
        # Caught by an actual live test, not a unit test with fixture data
        # that happened to always supply both scores.
        if earnings_score is not None:
            combined_score = (macro_score + earnings_score) / 2
            confidence = 1.0
        else:
            combined_score = macro_score
            confidence = 0.6

        return AgentOutput(
            agent_name=self.name,
            score=combined_score,
            confidence=confidence,
            rationale=f"Macro: {macro_rationale} | Earnings: {earnings_rationale}",
            raw_data={"macro": macro_data},
        )

    def _pull_macro_series(self) -> dict:
        results = {}
        for label, series_id in FRED_SERIES.items():
            try:
                resp = requests.get(FRED_API_BASE, params={
                    "series_id": series_id,
                    "api_key": self.fred_api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 2,  # latest + prior, for a simple direction check
                })
                resp.raise_for_status()
                obs = resp.json().get("observations", [])
                results[label] = obs
            except requests.RequestException as e:
                results[label] = {"error": str(e)}
        return results

    def _score_macro(self, macro_data: dict) -> tuple[float, str]:
        """Placeholder scoring logic — deliberately simple and transparent
        rather than a black box, so it can be argued with and tuned. This is
        NOT a calibrated model yet; treat the score as a rough direction
        signal until backtested.

        Two components, averaged:
          1. Yield curve slope regime (existing) — recession signal.
          2. 10Y yield direction (new) — discount-rate/tech-sensitivity signal.
        These are deliberately kept separate and simply averaged rather than
        weighted, for the same "argue with it, don't trust it yet" reason as
        the rest of this agent. Revisit the weighting once backtested.
        """
        curve_score, curve_rationale = self._score_yield_curve(macro_data)
        rate_score, rate_rationale = self._score_10y_direction(macro_data)

        combined = (curve_score + rate_score) / 2
        return combined, f"{curve_rationale} | {rate_rationale}"

    def _score_yield_curve(self, macro_data: dict) -> tuple[float, str]:
        """Curve-shape regime signal (10Y-2Y spread). Unchanged from the
        original implementation — kept as its own method so it can be
        tested/tuned independently of the new rate-direction signal."""
        yc = macro_data.get("yield_curve_10y2y", [])
        if isinstance(yc, list) and len(yc) >= 1:
            try:
                latest_spread = float(yc[0]["value"])
                if latest_spread < 0:
                    return -30.0, f"Yield curve inverted ({latest_spread:.2f}), historically bearish signal"
                else:
                    return 10.0, f"Yield curve positive ({latest_spread:.2f}), normal regime"
            except (ValueError, KeyError):
                pass
        return 0.0, "Yield curve data unavailable, neutral default"

    def _score_10y_direction(self, macro_data: dict) -> tuple[float, str]:
        """10Y Treasury yield level/direction — the discount-rate channel
        that hits long-duration growth/tech names (NDX) harder than the
        broader market. This is intentionally SEPARATE from the curve-slope
        signal above: a positive/normal curve says nothing about whether
        the 10Y itself just spiked, which is what actually pressures growth
        valuations. Placeholder thresholds, same caveat as _score_yield_curve
        — not backtested, meant to be argued with and tuned.

        Scoring: compares the two most recent observations from FRED
        (limit=2 in _pull_macro_series). A rising 10Y -> bearish for
        growth/tech (negative score). A falling 10Y -> bullish (positive).
        Magnitude is scaled by basis-point move, capped at +/-25 so this
        single signal can't dominate the composite on its own.
        """
        dgs10 = macro_data.get("treasury_10y", [])
        if not isinstance(dgs10, list) or len(dgs10) < 2:
            return 0.0, "10Y yield data unavailable, neutral default"
        try:
            latest = float(dgs10[0]["value"])
            prior = float(dgs10[1]["value"])
        except (ValueError, KeyError):
            return 0.0, "10Y yield data unparseable, neutral default"

        change_bps = (latest - prior) * 100  # percentage points -> bps
        # Scale: 10bps move -> 10 points of score, capped at +/-25.
        # Sign inverted vs. the move itself: rising yield = bearish for tech.
        raw_score = -change_bps
        capped_score = max(-25.0, min(25.0, raw_score))

        direction = "rising" if change_bps > 0 else "falling" if change_bps < 0 else "flat"
        return capped_score, (
            f"10Y yield {direction} ({change_bps:+.0f}bps: {prior:.2f}->{latest:.2f}), "
            f"{'headwind' if change_bps > 0 else 'tailwind' if change_bps < 0 else 'neutral'} "
            f"for long-duration growth/tech"
        )

    def _earnings_tone(self, context: dict) -> tuple[Optional[float], str]:
        """Hook for the existing LLM synthesis layer to fill in an
        earnings-season aggregate tone score. Left unimplemented here since
        it depends on wiring into the already-existing agents/llm/ module
        (per the market-prediction-engine repo) rather than duplicating it.
        """
        if not context.get("earnings_season_active"):
            return None, "Not earnings season — sub-signal not applicable this week"
        return None, "Earnings tone scoring not yet wired to LLM synthesis layer — TODO"

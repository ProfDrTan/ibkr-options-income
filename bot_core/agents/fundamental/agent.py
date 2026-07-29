"""
Fundamental Agent — see docs/ARCHITECTURE_DECISIONS.md ADR-003 for scope
rationale (hard macro data + LLM-synthesized earnings tone, deliberately NOT
a paid earnings-data subscription).

Two sub-signals, averaged into one AgentOutput:
  1. Macro data signal — pulled from FRED (free, no API cost)
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
        """
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

    def _earnings_tone(self, context: dict) -> tuple[Optional[float], str]:
        """Hook for the existing LLM synthesis layer to fill in an
        earnings-season aggregate tone score. Left unimplemented here since
        it depends on wiring into the already-existing agents/llm/ module
        (per the market-prediction-engine repo) rather than duplicating it.
        """
        if not context.get("earnings_season_active"):
            return None, "Not earnings season — sub-signal not applicable this week"
        return None, "Earnings tone scoring not yet wired to LLM synthesis layer — TODO"

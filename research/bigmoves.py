"""Big-move test (mandatory for every entry-rule change, 26 Nov 2026):
for each market's 15 largest daily moves in the 2-year history, did the rules trade it and what was the P&L?
Usage: python research/bigmoves.py   (run from research/ with data in ../data/history)"""
import pandas as pd
from engine import load, backtest
from strategies import squeeze
from cont import day_break
H = "../data/history/"
RULES = {"squeeze only": lambda d: squeeze(d, 40, .2),
         "squeeze + 24h breakout": lambda d: squeeze(d, 40, .2).where(squeeze(d, 40, .2) != 0, day_break(d, 96))}
for s in ["MGC", "MNQ", "MYM"]:
    d = load(s, H + f"{s}_15m.csv.gz"); dc = d.close.resample("1D").last().dropna().pct_change().dropna()
    big = [x.tz_localize(None) if x.tzinfo else x for x in dc.abs().sort_values(ascending=False).head(15).index]
    for name, f in RULES.items():
        t = backtest(d, f(d), s); e = t.entry.dt.tz_convert(None).dt.normalize(); x = t.exit.dt.tz_convert(None).dt.normalize()
        hit = [((e <= b) & (x >= b)) for b in big]
        print(f"{s} {name:24s} traded {sum(h.any() for h in hit):2d}/15 big days · P&L ${sum(t[h].pnl.sum() for h in hit):,.0f}")

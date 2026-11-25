"""Event backtest matching the live bot's exits: enter next bar open (+1 tick), initial stop 1.5 ATR,
trail 3 ATR from best price (IBKR TRAIL), weekend flatten Fri 20:30 UTC, exit on contract roll.
Intrabar: stop checked before the bar's favourable extreme updates the trail (conservative).
Costs: 1 tick slippage each way + $0.50 round-trip commission per micro contract."""
import numpy as np, pandas as pd, gzip
SPEC = {"MGC":(10,0.10),"MNQ":(2,0.25),"MYM":(0.5,1.0),"M2K":(5,0.10),"MCL":(100,0.01),"ZN":(1000,0.015625),"MBT":(0.1,5.0)}

def load(sym, path):
    df = pd.read_csv(path, parse_dates=["time_utc"]).set_index("time_utc").sort_index()
    df = df[~df.index.duplicated()]
    return df

def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([df.high-df.low, (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def backtest(df, sig, sym, init_atr=1.5, trail_atr=3.0, maxbars=None):
    """sig: Series in {-1,0,1} evaluated at bar close. One position at a time."""
    mult, tick = SPEC[sym]
    A = atr(df).values; o,h,l,c = (df[k].values for k in "open high low close".split())
    con = df.contract.values; t = df.index; s = sig.reindex(df.index).fillna(0).values
    fri = (t.dayofweek == 4) & ((t.hour*60+t.minute) >= 20*60+30)
    late = (t.dayofweek == 4) & ((t.hour*60+t.minute) >= 18*60+30)
    trades = []; pos = 0; i = 1; n = len(df)
    while i < n-1:
        if pos == 0:
            if s[i] != 0 and not late[i] and con[i+1] == con[i] and A[i] > 0:
                pos = int(s[i]); e = o[i+1] + pos*tick; a = A[i]; risk = init_atr*a
                stop = e - pos*risk; best = e; j = i+1; ei = i+1
                while True:
                    # stop check
                    if pos == 1 and l[j] <= stop: x = min(o[j], stop) - tick; why="stop"; break
                    if pos == -1 and h[j] >= stop: x = max(o[j], stop) + tick; why="stop"; break
                    best = max(best, h[j]) if pos == 1 else min(best, l[j])
                    stop = max(stop, best - trail_atr*a) if pos == 1 else min(stop, best + trail_atr*a)
                    if fri[j] or j+1 >= n or con[j+1] != con[j] or (maxbars and j-ei >= maxbars):
                        x = c[j] - pos*tick; why = "time"; break
                    j += 1
                pnl = pos*(x-e)*mult - 0.5
                trades.append((t[ei], t[j], pos, e, x, pnl, pnl/(risk*mult), why))
                pos = 0; i = j+1; continue
        i += 1
    return pd.DataFrame(trades, columns="entry exit side ep xp pnl R why".split())

def stats(tr):
    if len(tr) == 0: return dict(n=0, R=0, totR=0, win=0, pf=0, dd=0)
    eq = tr.R.cumsum(); dd = (eq.cummax()-eq).max()
    g, ls = tr.R[tr.R>0].sum(), -tr.R[tr.R<0].sum()
    return dict(n=len(tr), R=round(tr.R.mean(),3), totR=round(tr.R.sum(),1), win=round((tr.R>0).mean(),2),
                pf=round(g/ls,2) if ls else 99, ddR=round(dd,1), usd=round(tr.pnl.sum()))

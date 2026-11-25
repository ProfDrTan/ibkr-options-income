import numpy as np, pandas as pd
from engine import atr

def ema(x, n): return x.ewm(span=n, adjust=False).mean()
def rsi(x, n):
    d = x.diff(); u = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean(); v = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1+u/v)
def er(x, n):  # Kaufman efficiency ratio
    return (x - x.shift(n)).abs() / x.diff().abs().rolling(n).sum()
def htf_trend(df, f=50, s=200, k=4):  # ~1h EMA50/200 using 15m bars x4
    return np.sign(ema(df.close, f*k) - ema(df.close, s*k))

def donchian(df, n=20):
    hi = df.high.rolling(n).max().shift(); lo = df.low.rolling(n).min().shift()
    return pd.Series(np.where(df.close > hi, 1, np.where(df.close < lo, -1, 0)), df.index)

def donchian_trend(df, n=20, er_min=0.0, k=4):
    b = donchian(df, n); tr = htf_trend(df, k=k)
    ok = er(df.close, 20*k) >= er_min
    return b.where((b == tr) & ok, 0)

def squeeze(df, n=20, pct=0.2, look=500, trend=True):
    m = df.close.rolling(n).mean(); sd = df.close.rolling(n).std()
    w = (4*sd/m); rank = w.rolling(look).rank(pct=True)
    sq = (rank < pct).rolling(6).max().astype(bool)
    b = donchian(df, n).where(sq, 0)
    return b.where(b == htf_trend(df), 0) if trend else b

def pullback(df, rlen=2, lo=10, hi=90):
    tr = htf_trend(df); r = rsi(df.close, rlen)
    dip = (r < lo).rolling(4).max().astype(bool); pop = (r > hi).rolling(4).max().astype(bool)
    above = df.close > df.high.shift(); below = df.close < df.low.shift()
    e50 = ema(df.close, 200)  # ~1h EMA50
    long = (tr == 1) & (df.close > e50) & dip & above
    short = (tr == -1) & (df.close < e50) & pop & below
    return pd.Series(np.where(long, 1, np.where(short, -1, 0)), df.index)

def orb(df, sess="ny", bars=2, window=8, trend=True):
    """Opening-range breakout. ny=09:30 New York, ldn=08:00 London, asia=08:00 Singapore."""
    tz = {"ny":"America/New_York","ldn":"Europe/London","asia":"Asia/Singapore"}[sess]
    start = {"ny":(9,30),"ldn":(8,0),"asia":(8,0)}[sess]
    lt = df.index.tz_convert(tz); mins = lt.hour*60+lt.minute; s0 = start[0]*60+start[1]
    day = pd.Series(lt.date, df.index)
    inor = (mins >= s0) & (mins < s0+15*bars)
    after = (mins >= s0+15*bars) & (mins < s0+15*(bars+window))
    orh = df.high.where(inor).groupby(day).transform("max"); orl = df.low.where(inor).groupby(day).transform("min")
    up = after & (df.close > orh); dn = after & (df.close < orl)
    sig = pd.Series(np.where(up, 1, np.where(dn, -1, 0)), df.index)
    # first break of the day only
    first = sig.ne(0) & ~sig.ne(0).groupby(day).cumsum().gt(1)
    sig = sig.where(first, 0)
    return sig.where(sig == htf_trend(df), 0) if trend else sig

def vol_breakout(df, n=20, vmult=1.5, trend=True):
    b = donchian(df, n); v = df.volume > vmult*df.volume.rolling(96).mean().shift()
    b = b.where(v, 0)
    return b.where(b == htf_trend(df), 0) if trend else b

def squeeze_vol(df, n=20, pct=0.2, vmult=1.3):
    b = squeeze(df, n, pct); v = df.volume > vmult*df.volume.rolling(96).mean().shift()
    return b.where(v, 0)

def nr7(df, trend=True):
    """Day after the narrowest daily range of 7: break of that day's high/low."""
    d = df.resample("1D").agg({"high":"max","low":"min"}).dropna()
    rng = d.high-d.low; isnr = rng <= rng.rolling(7).min()
    lvl = pd.DataFrame({"h":d.high.where(isnr),"l":d.low.where(isnr)}).shift(1)  # known next day
    lvl = lvl.reindex(df.index, method="ffill")
    day = df.index.floor("1D"); fresh = lvl.notna().all(axis=1)
    lvl_day = pd.Series(df.index.floor("1D"), df.index)
    up = df.close > lvl.h; dn = df.close < lvl.l
    # only on the day right after the NR7 day
    nrday = pd.Series(isnr.shift(1).fillna(False).astype(bool).reindex(df.index.floor("1D")).values, df.index)
    sig = pd.Series(np.where(up & nrday, 1, np.where(dn & nrday, -1, 0)), df.index)
    first = sig.ne(0) & ~sig.ne(0).groupby(lvl_day).cumsum().gt(1)
    sig = sig.where(first, 0)
    return sig.where(sig == htf_trend(df), 0) if trend else sig

def fade(df, n=20, k=2.5, er_max=0.25, rlen=2):
    m = df.close.rolling(n).mean(); sd = df.close.rolling(n).std(); r = rsi(df.close, rlen)
    chop = er(df.close, 80) < er_max
    turn_up = df.close > df.high.shift(); turn_dn = df.close < df.low.shift()
    wasdn = (df.low < m-k*sd).rolling(3).max().astype(bool); wasup = (df.high > m+k*sd).rolling(3).max().astype(bool)
    long = chop & wasdn & turn_up; short = chop & wasup & turn_dn
    return pd.Series(np.where(long, 1, np.where(short, -1, 0)), df.index)

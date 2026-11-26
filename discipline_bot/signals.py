"""
discipline_bot/signals.py: entry rules, pure Python (no pandas needed on the Lenovo).

SQUEEZE BREAKOUT WITH TREND (chosen 25 Nov 2026 from a 2-year, 15-minute walk-forward test):
  1. Squeeze: the 40-bar Bollinger band width (4 x stdev / mean) ranked in the lowest 20% of the
     last 500 bars, at any time in the last 6 bars. The market has gone quiet and coiled.
  2. Breakout: the bar closes above the highest high (long) or below the lowest low (short) of
     the prior 40 bars.
  3. Trend: only in the direction of the 1-hour trend, EMA(200) vs EMA(800) of 15-minute closes
     (about EMA 50 vs 200 on 1-hour bars).
Exits are unchanged: initial stop 1.5 ATR, trailing 3 ATR, no profit target.
Must stay identical to research/strategies.py squeeze(n=40, pct=0.2) and is checked against it.
"""
import math

N = 40          # breakout and band lookback
PCT = 0.20      # squeeze = band width in lowest 20%
LOOK = 500      # bars used to rank band width
RECENT = 6      # squeeze must have occurred within the last 6 bars
FAST, SLOW = 200, 800
MIN_BARS = SLOW * 2   # EMA warm-up


def _ema(xs, n):
    a, out, e = 2 / (n + 1), [], None
    for x in xs:
        e = x if e is None else a * x + (1 - a) * e
        out.append(e)
    return out


def _widths(closes, n):
    out = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        w = closes[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / (n - 1))
        out[i] = 4 * sd / m
    return out


def _pct_rank(window):
    """pandas rank(pct=True) of the last value, ties averaged."""
    v = window[-1]
    less = sum(1 for x in window if x < v)
    eq = sum(1 for x in window if x == v)
    return (less + (eq + 1) / 2) / len(window)


def squeeze_signal(highs, lows, closes):
    """Signal on the LAST completed bar: +1 long, -1 short, 0 none, plus a reason string."""
    k = len(closes)
    if k < MIN_BARS:
        return 0, f"warming up ({k}/{MIN_BARS} bars)"
    wid = _widths(closes, N)
    squeezed = False
    for j in range(k - RECENT, k):
        win = [w for w in wid[j - LOOK + 1:j + 1] if w is not None]
        if len(win) == LOOK and _pct_rank(win) < PCT:
            squeezed = True
            break
    if not squeezed:
        return 0, "no squeeze"
    hi, lo = max(highs[-N - 1:-1]), min(lows[-N - 1:-1])
    c = closes[-1]
    brk = 1 if c > hi else -1 if c < lo else 0
    if not brk:
        return 0, "squeeze, no breakout"
    f, s = _ema(closes, FAST)[-1], _ema(closes, SLOW)[-1]
    trend = 1 if f > s else -1 if f < s else 0
    if brk != trend:
        return 0, "breakout against 1h trend"
    return brk, (f"squeeze breakout {'above' if brk > 0 else 'below'} 40-bar {'high' if brk > 0 else 'low'} "
                 f"{hi if brk > 0 else lo}, 1h trend {'up' if trend > 0 else 'down'}")


# 24-HOUR BREAKOUT WITH TREND (added 26 Nov 2026; catches trends already under way, when there is no squeeze):
#   the bar closes beyond the highest high / lowest low of the prior 96 bars (24 hours), in the direction
#   of the same 1-hour trend. 2-year test, combined with the squeeze rule: MGC +0.10/+0.24R, MNQ +0.29/+0.13R,
#   MYM +0.20/+0.04R per trade (year 1/year 2). Identical to research/cont.py day_break(n=96).
DAY = 96


def daybreak_signal(highs, lows, closes, fast=FAST, slow=SLOW):
    """fast/slow: trend EMAs. MGC and MNQ use the faster 100/400 (about 1h EMA 25/100), MYM keeps 200/800."""
    k = len(closes)
    if k < MIN_BARS:
        return 0, f"warming up ({k}/{MIN_BARS} bars)"
    hi, lo = max(highs[-DAY - 1:-1]), min(lows[-DAY - 1:-1])
    c = closes[-1]
    brk = 1 if c > hi else -1 if c < lo else 0
    if not brk:
        return 0, "no 24h breakout"
    f, s = _ema(closes, fast)[-1], _ema(closes, slow)[-1]
    trend = 1 if f > s else -1 if f < s else 0
    if brk != trend:
        return 0, "24h breakout against trend"
    return brk, (f"24h breakout {'above' if brk > 0 else 'below'} 24-hour {'high' if brk > 0 else 'low'} "
                 f"{hi if brk > 0 else lo}, 1h trend {'up' if trend > 0 else 'down'}")


# REVERSAL AFTER A CRASH OR SPIKE (added 26 Nov 2026, Nasdaq and Dow only):
#   in the last 4 hours price made a low that is more than 12 "normal" ATRs below the 24-hour high (normal ATR =
#   median 15-min ATR of the last 2 days, so the crash itself doesn't shrink the yardstick); then a bar closes above
#   the highest high of the previous 8 bars (2 hours) -> LONG. Mirror for a spike up -> SHORT. No trend filter and
#   no VIX guard: the point is to catch the turn. 2-year test with the other two rules: MNQ +0.26/+0.01R,
#   MYM +0.18/+0.05R per trade; replay of 25-26 Nov: long MNQ 01:15 SGT @ 25,532. research/rev.py rev2(K=12, N=8, win=16).
REV_K, REV_N, REV_WIN, REV_LOOK, REV_MED = 12, 8, 16, 96, 192


def _atr_series(highs, lows, closes, n=14):
    out, a = [], None
    for i in range(len(closes)):
        tr = highs[i] - lows[i] if i == 0 else max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        a = tr if a is None else a + (tr - a) / n
        out.append(a)
    return out


def reversal_signal(highs, lows, closes):
    k = len(closes)
    if k < REV_MED + REV_LOOK + 2:
        return 0, "warming up"
    atrs = _atr_series(highs, lows, closes)
    p = k - 2                                     # conditions measured on the previous bar
    med = sorted(atrs[p - REV_MED + 1:p + 1])
    m = (med[REV_MED // 2 - 1] + med[REV_MED // 2]) / 2
    hi_day, lo_day = max(highs[p - REV_LOOK + 1:p + 1]), min(lows[p - REV_LOOK + 1:p + 1])
    lo_rec, hi_rec = min(lows[p - REV_WIN + 1:p + 1]), max(highs[p - REV_WIN + 1:p + 1])
    c = closes[-1]
    if (hi_day - lo_rec) / m > REV_K and c > max(highs[-REV_N - 1:-1]):
        return 1, f"reversal up: fell {(hi_day - lo_rec) / m:.0f} ATR to {lo_rec:g}, now closed above 2-hour high"
    if (hi_rec - lo_day) / m > REV_K and c < min(lows[-REV_N - 1:-1]):
        return -1, f"reversal down: rose {(hi_rec - lo_day) / m:.0f} ATR to {hi_rec:g}, now closed below 2-hour low"
    return 0, "no reversal"

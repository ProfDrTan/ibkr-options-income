"""Liquidity sweep + fast-EMA reclaim. Swing high = highest high of the previous N bars (a level stops sit above).
Sweep: a bar trades above it; within the next K bars price closes back below the level AND below both EMA 9 and 21
-> SHORT (mirror for lows). Stop: sweep extreme + 1 tick. Exits: trailing 3 ATR (system style) or fixed 2R target."""
import pandas as pd, numpy as np
from engine import SPEC, atr
from strategies import ema
def signals(d, N=20, K=3):
    hi=d.high.rolling(N).max().shift(); lo=d.low.rolling(N).min().shift()
    e9,e21=ema(d.close,9),ema(d.close,21)
    swept_hi=(d.high>hi); swept_lo=(d.low<lo)
    lvl_hi=hi.where(swept_hi).ffill(limit=K); lvl_lo=lo.where(swept_lo).ffill(limit=K)
    ext_hi=d.high.where(swept_hi).ffill(limit=K); ext_lo=d.low.where(swept_lo).ffill(limit=K)
    S=(lvl_hi.notna())&(d.close<lvl_hi)&(d.close<e9)&(d.close<e21)&~swept_hi
    L=(lvl_lo.notna())&(d.close>lvl_lo)&(d.close>e9)&(d.close>e21)&~swept_lo
    sig=pd.Series(np.where(L,1,np.where(S,-1,0)),d.index)
    sig=sig.where(sig.ne(sig.shift()),0)      # first bar only
    stop=pd.Series(np.where(sig==-1,ext_hi,np.where(sig==1,ext_lo,np.nan)),d.index)
    return sig,stop
def run(d,sym,N=20,K=3,exit="trail"):
    mult,tick=SPEC[sym]; sig,stp=signals(d,N,K); A=atr(d).values
    o,h,l,c=(d[k].values for k in "open high low close".split()); s=sig.values; sv=stp.values; t=d.index; out=[]; i=0
    while i<len(d)-1:
        if s[i]!=0 and not np.isnan(sv[i]):
            pos=int(s[i]); e=o[i+1]+pos*tick; st=sv[i]-pos*tick; risk=abs(e-st)
            if risk<=0 or risk>6*A[i]: i+=1; continue
            best=e; x=None; tgt=e+pos*2*risk
            for j in range(i+1,len(d)):
                if (pos==1 and l[j]<=st) or (pos==-1 and h[j]>=st): x=st-pos*tick; break
                if exit=="2R" and ((pos==1 and h[j]>=tgt) or (pos==-1 and l[j]<=tgt)): x=tgt; break
                if exit=="trail":
                    best=max(best,h[j]) if pos==1 else min(best,l[j]); st=max(st,best-3*A[i]) if pos==1 else min(st,best+3*A[i])
                if j-i>=96*3: x=c[j]; break
            if x is None: break
            pnl=pos*(x-e)*mult-0.5; out.append((t[i],pos,e,x,pnl,pnl/(risk*mult))); i=j+1; continue
        i+=1
    return pd.DataFrame(out,columns="entry side ep xp pnl R".split())
if __name__=="__main__":
    from engine import load
    d=load("MBT","/home/claude/ibkr-options-income/data/history/MBT_15m.csv.gz"); mid=d.index[0]+(d.index[-1]-d.index[0])/2
    for N in (20,40):
        for ex in ("trail","2R"):
            t=run(d,"MBT",N,3,ex); a,b=t[t.entry<mid],t[t.entry>=mid]
            print(f"MBT 15m swing{N} exit={ex:5s} y1 {a.R.mean():+.2f}R ${a.pnl.sum():>7,.0f} | y2 {b.R.mean():+.2f}R ${b.pnl.sum():>7,.0f} | n{len(t)} win {(t.pnl>0).mean():.0%}")

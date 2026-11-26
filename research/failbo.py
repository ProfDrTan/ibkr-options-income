"""Fade failed breakouts of the prior regular session's high/low (09:30-16:00 NY), on 5-min bars during the session.
Break: 5m bar trades beyond prior high/low; within 3 bars a 5m bar closes back inside -> fade. Stop beyond the extreme.
Exit: session VWAP (target) or 2x risk trailing; flat 15:55 NY."""
import pandas as pd, numpy as np
from engine import SPEC
from intraday import load1
def run(sym, exit="vwap"):
    mult,tick=SPEC[sym]; m=load1(sym); out=[]
    days=sorted(set(m.index.date)); prev=None
    for day in days:
        g=m[m.index.date==day]
        if len(g)<300: prev=g; continue
        if prev is None or len(prev)<300: prev=g; continue
        ph,pl=prev.high.max(),prev.low.min()
        tp=(g.high+g.low+g.close)/3; v=g.volume.clip(lower=1); vw=((tp*v).cumsum()/v.cumsum())
        b=g.resample("5min").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
        vw5=vw.resample("5min").last().reindex(b.index).values
        o,h,l,c=(b[k].values for k in "open high low close".split()); t=b.index; done=False
        for i in range(len(b)-1):
            if done or t[i].time()>=pd.Timestamp("15:00").time(): break
            for lvl,side in ((ph,-1),(pl,1)):
                lo_i=max(0,i-3)
                broke=(h[lo_i:i+1].max()>lvl) if side==-1 else (l[lo_i:i+1].min()<lvl)
                back=(c[i]<lvl) if side==-1 else (c[i]>lvl)
                if broke and back and ((side==-1 and c[i-1]>=lvl if i else False) or (side==1 and c[i-1]<=lvl if i else False) or True):
                    ext=h[lo_i:i+1].max() if side==-1 else l[lo_i:i+1].min()
                    e=o[i+1]+side*tick; st=ext-side*tick*(-1) if False else (ext+tick if side==-1 else ext-tick); risk=abs(e-st)
                    if risk<=0: continue
                    best=e; x=None
                    for j in range(i+1,len(b)):
                        if (side==1 and l[j]<=st) or (side==-1 and h[j]>=st): x=st-side*tick; break
                        if exit=="vwap" and ((side==1 and h[j]>=vw5[j]) or (side==-1 and l[j]<=vw5[j])) and abs(vw5[j]-e)>0: x=vw5[j]; break
                        if exit=="trail":
                            best=max(best,h[j]) if side==1 else min(best,l[j]); st=max(st,best-2*risk) if side==1 else min(st,best+2*risk)
                        if t[j].time()>=pd.Timestamp("15:55").time(): x=c[j]-side*tick; break
                    if x is None: x=c[-1]
                    pnl=side*(x-e)*mult-0.5; out.append((t[i],side,e,x,pnl,pnl/(risk*mult))); done=True; break
        prev=g
    return pd.DataFrame(out,columns="entry side ep xp pnl R".split())

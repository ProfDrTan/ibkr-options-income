import pandas as pd, numpy as np
from engine import *; from strategies import ema, htf_trend
from cont import D
from confirm import allsig
from pyr import bt
def cross(d,f=8,s=21,k=1):
    a,b=ema(d.close,f*k),ema(d.close,s*k); x=np.sign(a-b)
    return pd.Series(np.where((x==1)&(x.shift()==-1),1,np.where((x==-1)&(x.shift()==1),-1,0)),d.index)
def state(d,f=8,s=21,k=1): return np.sign(ema(d.close,f*k)-ema(d.close,s*k))
tot={}
for s,d in D.items():
    base=allsig(d,s); mid=d.index[0]+(d.index[-1]-d.index[0])/2
    dc=d.close.resample("1D").last().dropna().pct_change().dropna(); big=dc.abs().sort_values(ascending=False).head(15).index
    V={"current system":base,
       "8/21 cross alone (15m)":cross(d),
       "8/21 cross alone (1h)":cross(d,k=4),
       "8/21 cross + 1h trend":cross(d).where(cross(d)==htf_trend(d,k=2),0),
       "current, only if 8/21 (15m) agrees":base.where((base==0)|(base==state(d)),0),
       "current, only if 8/21 (1h) agrees":base.where((base==0)|(base==state(d,k=4)),0),
       "current + 8/21 cross as extra entry":base.where(base!=0,cross(d).where(cross(d)==htf_trend(d,k=2),0))}
    for nm,sig in V.items():
        t=bt(d,sig,s,L=4); e=t.entry.dt.floor("1D"); x=t.exit.dt.floor("1D"); hit=[((e<=b)&(x>=b)) for b in big]
        print(s,f"{nm:38s} y1 ${t[t.entry<mid].pnl.sum():>8,.0f} y2 ${t[t.entry>=mid].pnl.sum():>8,.0f} big {sum(h.any() for h in hit)}/15 ${sum(t[h].pnl.sum() for h in hit):>7,.0f}",flush=True)
        tot[nm]=tot.get(nm,0)+t.pnl.sum()
for k,v in tot.items(): print("ALL",f"{k:38s} ${v:,.0f}")

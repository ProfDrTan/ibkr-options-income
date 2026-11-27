import pandas as pd, numpy as np
from engine import *; from strategies import *
from cont import D
from rev import rev2
from pyr import bt
from emax import cross
def sysig(d,s,mode):
    k={"MGC":2,"MNQ":2,"MYM":4}[s]; f,sl=50*k,200*k
    ef,es=ema(d.close,f),ema(d.close,sl); tr=np.sign(ef-es)
    db=donchian(d,96)
    if mode=="override":   # also allow a 24h breakout when price is beyond BOTH trend EMAs and the fast EMA has turned
        turned=np.sign(ef-ef.shift(8))
        alt=np.where((d.close>ef)&(d.close>es)&(turned>0),1,np.where((d.close<ef)&(d.close<es)&(turned<0),-1,0))
        ok=(db==tr)|(db==alt)
    else: ok=(db==tr)
    db=db.where(ok,0); sq=squeeze(d,40,.2); b=sq.where(sq!=0,db)
    if s!="MGC": b=b.where(b!=0,rev2(d,12,8,96,16))
    else: c=cross(d); b=b.where(b!=0,c.where(c==htf_trend(d,k=2),0))
    return b
if __name__=="__main__":
    tot={}
    for s,d in D.items():
        mid=d.index[0]+(d.index[-1]-d.index[0])/2
        dc=d.close.resample("1D").last().dropna().pct_change().dropna(); big=dc.abs().sort_values(ascending=False).head(15).index
        for mode in ("current","override"):
            t=bt(d,sysig(d,s,mode),s,L=4); e=t.entry.dt.floor("1D"); x=t.exit.dt.floor("1D"); hit=[((e<=b)&(x>=b)) for b in big]
            eq=t.sort_values("exit").pnl.cumsum(); dd=(eq.cummax()-eq).max()
            print(s,f"{mode:8s} y1 ${t[t.entry<mid].pnl.sum():>8,.0f} y2 ${t[t.entry>=mid].pnl.sum():>8,.0f} big {sum(h.any() for h in hit)}/15 ${sum(t[h].pnl.sum() for h in hit):>7,.0f} dd ${dd:,.0f}",flush=True)
            tot[mode]=tot.get(mode,0)+t.pnl.sum()
    print("ALL",{k:round(v) for k,v in tot.items()})

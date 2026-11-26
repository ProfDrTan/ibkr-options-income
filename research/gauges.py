import pandas as pd, numpy as np
from engine import *; from strategies import ema
from cont import D
from confirm import allsig
from pyr import bt
H="/home/claude/ibkr-options-income/data/history/"
def gauges(sym, idx15, tfs=(1,2,3,4,5), n=20):
    m=pd.read_csv(H+f"{sym}_1m.csv.gz",parse_dates=["time_utc"]).set_index("time_utc").sort_index()
    m=m[~m.index.duplicated()]
    T=idx15+pd.Timedelta(minutes=15)          # moment the 15m bar closes
    out={}
    for tf in tfs:
        c=m.close.resample(f"{tf}min",label="right",closed="left").last().dropna()   # label = bar end
        tr=np.sign(c-ema(c,n))
        out[tf]=tr.reindex(T,method="ffill").values
    g=pd.DataFrame(out,index=idx15)
    return g, m.index[0]
def gate(sig,g,need=5):
    agree=(g.values==sig.values[:,None]).sum(axis=1)
    return sig.where((sig==0)|(agree>=need),0)
if __name__=="__main__":
    tot={}
    for s,d in D.items():
        g,start=gauges(s,d.index)
        dd=d[d.index>=start+pd.Timedelta(days=2)]; gg=g.loc[dd.index]
        base=allsig(d,s).loc[dd.index]
        mid=dd.index[0]+(dd.index[-1]-dd.index[0])/2
        dc=dd.close.resample("1D").last().dropna().pct_change().dropna(); big=dc.abs().sort_values(ascending=False).head(15).index
        for nm,sig in [("no gauges",base),("5 of 5 agree",gate(base,gg,5)),("4 of 5 agree",gate(base,gg,4)),("3 of 5 agree",gate(base,gg,3))]:
            t=bt(dd,sig,s,L=4); e=t.entry.dt.floor("1D"); x=t.exit.dt.floor("1D"); hit=[((e<=b)&(x>=b)) for b in big]
            quick=((t.exit-t.entry)<=pd.Timedelta("45min"))&(t.pnl<0)
            print(s,f"{nm:13s} H1 ${t[t.entry<mid].pnl.sum():>8,.0f} H2 ${t[t.entry>=mid].pnl.sum():>8,.0f} big {sum(h.any() for h in hit)}/15 ${sum(t[h].pnl.sum() for h in hit):>7,.0f} trades {len(t)} quick-losers {quick.sum()} ${t[quick].pnl.sum():,.0f}",flush=True)
            tot[nm]=tot.get(nm,0)+t.pnl.sum()
    print("ALL",{k:round(v) for k,v in tot.items()})

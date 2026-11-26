import pandas as pd, numpy as np
from engine import *; from strategies import ema
from cont import D
from confirm import allsig
from pyr import bt
H="/home/claude/ibkr-options-income/data/history/"
def run(delay):
    tot=0; res=[]
    for s,d in D.items():
        m=pd.read_csv(H+f"{s}_1m.csv.gz",parse_dates=["time_utc"]).set_index("time_utc").sort_index(); m=m[~m.index.duplicated()]
        dd=d[d.index>=m.index[0]+pd.Timedelta(days=2)]
        base=allsig(d,s).loc[dd.index]
        T=dd.index+pd.Timedelta(minutes=15+delay)          # decision time
        px=m.close.reindex(T- pd.Timedelta(minutes=1),method="ffill").values   # 1m bar ending at T
        ok=np.ones(len(dd),bool)
        if delay>0:
            for tf in (1,2,3,4,5):
                c=m.close.resample(f"{tf}min",label="right",closed="left").last().dropna()
                tr=np.sign(c-ema(c,20)).reindex(T,method="ffill").values
                ok&=(tr==base.values)
        sig=base.where((base==0)|ok,0)
        epx=pd.Series(px,index=dd.index).values if delay>0 else None
        mid=dd.index[0]+(dd.index[-1]-dd.index[0])/2
        t=bt(dd,sig,s,L=4,epx=epx)
        dc=dd.close.resample("1D").last().dropna().pct_change().dropna(); big=dc.abs().sort_values(ascending=False).head(15).index
        e=t.entry.dt.floor("1D"); x=t.exit.dt.floor("1D"); hit=[((e<=b)&(x>=b)) for b in big]
        quick=((t.exit-t.entry)<=pd.Timedelta("45min"))&(t.pnl<0)
        print(s,f"wait {delay:2d}m H1 ${t[t.entry<mid].pnl.sum():>8,.0f} H2 ${t[t.entry>=mid].pnl.sum():>8,.0f} big {sum(h.any() for h in hit)}/15 trades {len(t)} quick-losers {quick.sum()} ${t[quick].pnl.sum():,.0f}",flush=True)
        tot+=t.pnl.sum()
    print("ALL wait",delay,round(tot))
for dl in [0,3,5,10]: run(dl)

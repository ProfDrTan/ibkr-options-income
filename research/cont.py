import pandas as pd, numpy as np
from engine import *; from strategies import *
H="/home/claude/ibkr-options-income/data/history/"
def cont_er(d,n=20,e=0.3,k=80):
    b=donchian(d,n); return b.where((b==htf_trend(d))&(er(d.close,k)>=e),0)
def day_break(d,n=96,e=0.0):
    b=donchian(d,n); return b.where((b==htf_trend(d))&(er(d.close,80)>=e),0)
def pull(d,ema_n=80,brk=8):
    tr=htf_trend(d); m=ema(d.close,ema_n)
    touched_up=(d.low<=m).rolling(brk).max().astype(bool); touched_dn=(d.high>=m).rolling(brk).max().astype(bool)
    hi=d.high.rolling(brk).max().shift(); lo=d.low.rolling(brk).min().shift()
    L=(tr==1)&touched_up&(d.close>hi)&(d.close>m); S=(tr==-1)&touched_dn&(d.close<lo)&(d.close<m)
    return pd.Series(np.where(L,1,np.where(S,-1,0)),d.index)
def volx(d,n=20,atrk=1.0):  # expansion bar: range > 2x ATR closing at extreme, with trend
    a=atr(d).shift(); rng=d.high-d.low; tr=htf_trend(d)
    dn=(rng>2*a)&(d.close<d.low+0.25*rng)&(tr==-1)&(d.close<d.low.rolling(n).min().shift())
    up=(rng>2*a)&(d.close>d.high-0.25*rng)&(tr==1)&(d.close>d.high.rolling(n).max().shift())
    return pd.Series(np.where(up,1,np.where(dn,-1,0)),d.index)
C={"er20 .3":lambda d:cont_er(d,20,.3),"er20 .4":lambda d:cont_er(d,20,.4),"er40 .3":lambda d:cont_er(d,40,.3),
   "daybreak96":lambda d:day_break(d),"daybreak96 er.25":lambda d:day_break(d,96,.25),
   "pull80/8":lambda d:pull(d),"pull40/8":lambda d:pull(d,40),"pull80/16":lambda d:pull(d,80,16),
   "volx":lambda d:volx(d)}
rows=[]
D={s:load(s,H+f"{s}_15m.csv.gz") for s in ["MGC","MNQ","MYM"]}
for nm,f in C.items():
    r={"rule":nm}
    for s,d in D.items():
        t=backtest(d,f(d),s); mid=d.index[0]+(d.index[-1]-d.index[0])/2
        a,b=t[t.entry<mid],t[t.entry>=mid]
        r[s]=f"{a.R.mean():+.2f}/{b.R.mean():+.2f} n{len(t)}"
    rows.append(r)
pd.set_option("display.width",200); print(pd.DataFrame(rows).to_string(index=False))

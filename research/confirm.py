import pandas as pd, numpy as np
from engine import *; from strategies import *; from cont import D
from rev import rev2
from pyr import bt
def allsig(d,s):
    k={"MGC":2,"MNQ":2,"MYM":4}[s]; db=donchian(d,96); db=db.where(db==htf_trend(d,k=k),0); sq=squeeze(d,40,.2); b=sq.where(sq!=0,db)
    return b.where(b!=0,rev2(d,12,8,96,16)) if s!="MGC" else b
def F(d,sig,kind):
    rng=(d.high-d.low).replace(0,np.nan); loc=(d.close-d.low)/rng
    if kind=="none": return sig
    if kind=="close_loc": ok=((sig==1)&(loc>0.7))|((sig==-1)&(loc<0.3))
    if kind=="volume": ok=d.volume>d.volume.rolling(20).mean().shift()
    if kind=="ema20": ok=((sig==1)&(d.close>ema(d.close,20)))|((sig==-1)&(d.close<ema(d.close,20)))
    if kind=="2bar":  # previous bar also closed in the signal direction (momentum)
        ok=((sig==1)&(d.close.shift()>d.open.shift()))|((sig==-1)&(d.close.shift()<d.open.shift()))
    if kind=="loc+vol": ok=(((sig==1)&(loc>0.7))|((sig==-1)&(loc<0.3)))&(d.volume>d.volume.rolling(20).mean().shift())
    return sig.where(ok,0)
if __name__=="__main__":
    tot={}
    for s,d in D.items():
        base=allsig(d,s); mid=d.index[0]+(d.index[-1]-d.index[0])/2
        dc=d.close.resample("1D").last().dropna().pct_change().dropna(); big=dc.abs().sort_values(ascending=False).head(15).index
        for k in ["none","close_loc","volume","ema20","2bar","loc+vol"]:
            t=bt(d,F(d,base,k),s,L=4); e=t.entry.dt.floor("1D"); x=t.exit.dt.floor("1D"); hit=[((e<=b)&(x>=b)) for b in big]
            quick=((t.exit-t.entry)<=pd.Timedelta("45min"))&(t.pnl<0)
            print(s,f"{k:9s} y1 ${t[t.entry<mid].pnl.sum():>8,.0f} y2 ${t[t.entry>=mid].pnl.sum():>8,.0f} big ${sum(t[h].pnl.sum() for h in hit):>7,.0f} trades {len(t)} quick-losers {quick.sum()} (${t[quick].pnl.sum():,.0f})",flush=True)
            tot[k]=tot.get(k,0)+t.pnl.sum()
    print("ALL",{k:round(v) for k,v in tot.items()})

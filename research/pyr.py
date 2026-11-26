"""Scale-in / scale-out backtest: same-direction signal adds a lot (max L); each lot has its own stop
(1.5 ATR initial) and trail; newer lots trail tighter so they peel off first when the market turns.
Opposite signal closes all lots and reverses. Weekend flatten and contract roll close all."""
import numpy as np, pandas as pd
from engine import atr, SPEC
def bt(df, sig, sym, L=4, trails=(3.0,2.5,2.0,1.5), init=1.5, reverse=True, epx=None):
    mult,tick=SPEC[sym]; A=atr(df).values; o,h,l,c=(df[k].values for k in "open high low close".split())
    con=df.contract.values; t=df.index; s=sig.reindex(df.index).fillna(0).values
    fri=(t.dayofweek==4)&((t.hour*60+t.minute)>=20*60+30); late=(t.dayofweek==4)&((t.hour*60+t.minute)>=18*60+30)
    lots=[]; out=[]
    def close(lot,j,x,why): out.append((t[lot["ei"]],t[j],lot["side"],lot["e"],x,(x-lot["e"])*lot["side"]*mult-0.5,((x-lot["e"])*lot["side"])/lot["risk"],why,lot["n"]))
    for j in range(1,len(df)):
        # manage open lots on bar j
        for lot in list(lots):
            sd=lot["side"]
            if (sd==1 and l[j]<=lot["stop"]) or (sd==-1 and h[j]>=lot["stop"]):
                x=(min(o[j],lot["stop"])-tick) if sd==1 else (max(o[j],lot["stop"])+tick); close(lot,j,x,"stop"); lots.remove(lot); continue
            lot["best"]=max(lot["best"],h[j]) if sd==1 else min(lot["best"],l[j])
            lot["stop"]=max(lot["stop"],lot["best"]-lot["tr"]) if sd==1 else min(lot["stop"],lot["best"]+lot["tr"])
        if lots and (fri[j] or j+1>=len(df) or con[j+1]!=con[j]):
            for lot in lots: close(lot,j,c[j]-lot["side"]*tick,"time")
            lots=[]
        # new signal at close of bar j -> act at open j+1
        if j+1<len(df) and s[j]!=0 and not late[j] and con[j+1]==con[j] and A[j]>0:
            sd=int(s[j])
            if lots and lots[0]["side"]!=sd:
                if not reverse: continue
                for lot in lots: close(lot,j,c[j]-lot["side"]*tick,"reverse")
                lots=[]
            if len(lots)<L:
                n=len(lots); e=(epx[j] if epx is not None and not np.isnan(epx[j]) else o[j+1])+sd*tick; a=A[j]
                lots.append(dict(side=sd,e=e,ei=j+1,risk=init*a,stop=e-sd*init*a,best=e,tr=trails[min(n,len(trails)-1)]*a,n=n+1))
    return pd.DataFrame(out,columns="entry exit side ep xp pnl R why lot".split())

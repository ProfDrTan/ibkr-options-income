import pandas as pd, numpy as np
from engine import *; from strategies import *
from cont import day_break, D
VIX=pd.read_csv("/home/claude/ibkr-options-income/data/history/daily/VIX.csv",parse_dates=["date"]).set_index("date").close

def sessions(d):  # CME trading day: starts 22:00 UTC (17:00 CT approx)
    return (d.index - pd.Timedelta(hours=22)).floor("1D")

def profile(d, nbins=60, va=0.70):
    """Per session: POC, VAH, VAL from 15m bars, volume spread evenly over each bar's range."""
    out={}
    for day,g in d.groupby(sessions(d)):
        lo,hi=g.low.min(),g.high.max()
        if hi<=lo or g.volume.sum()<=0: continue
        edges=np.linspace(lo,hi,nbins+1); vol=np.zeros(nbins)
        for l,h,v in zip(g.low.values,g.high.values,g.volume.values):
            i0=max(0,np.searchsorted(edges,l,"right")-1); i1=min(nbins-1,np.searchsorted(edges,h,"right")-1)
            vol[i0:i1+1]+=v/(i1-i0+1)
        p=int(vol.argmax()); a=b=p; tot=vol[p]
        while tot<va*vol.sum():
            up=vol[b+1] if b+1<nbins else -1; dn=vol[a-1] if a>0 else -1
            if up>=dn: b+=1; tot+=up
            else: a-=1; tot+=dn
        mid=(edges[:-1]+edges[1:])/2
        out[day]=(mid[p],edges[b+1],edges[a])
    return pd.DataFrame(out,index=["poc","vah","val"]).T

def prior_profile(d):
    pr=profile(d).shift(1)   # yesterday's profile, known today
    s=sessions(d)
    return pr.reindex(s).set_axis(d.index)

def mp_break(d, need_trend=True):
    pr=prior_profile(d); tr=htf_trend(d)
    up=(d.close>pr.vah)&(d.close.shift()<=pr.vah); dn=(d.close<pr.val)&(d.close.shift()>=pr.val)
    sig=pd.Series(np.where(up,1,np.where(dn,-1,0)),d.index)
    return sig.where(sig==tr,0) if need_trend else sig

def vix_dir(d, n=10):
    v=VIX.copy(); v.index=v.index.tz_localize("UTC") if v.index.tz is None else v.index
    reg=np.sign(v-v.rolling(n).mean()).shift(1)       # yesterday's VIX vs its 10-day average, known today
    return reg.reindex(d.index.floor("1D")).set_axis(d.index).fillna(0)

def vix_filter(d, sig, index_only=True):
    r=vix_dir(d)  # +1 VIX rising (fear) -> shorts only ; -1 falling -> longs only
    return sig.where(~(((r==1)&(sig==1))|((r==-1)&(sig==-1))),0)

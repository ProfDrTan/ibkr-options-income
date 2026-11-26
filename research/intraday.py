"""1-minute intraday tests on the US cash session (09:30-16:00 New York): opening-range breakout and VWAP fade.
Fills: next 1m bar open +/- 1 tick; stops +/- 1 tick; $0.50 round trip. Flat by 15:55 NY."""
import pandas as pd, numpy as np
from engine import SPEC
H="/home/claude/ibkr-options-income/data/history/"
def load1(sym):
    m=pd.read_csv(H+f"{sym}_1m.csv.gz",parse_dates=["time_utc"]).set_index("time_utc").sort_index()
    m=m[~m.index.duplicated()]
    m.index=m.index.tz_convert("America/New_York")
    return m[(m.index.time>=pd.Timestamp("09:30").time())&(m.index.time<pd.Timestamp("16:00").time())]
def orb(sym, m, mins=15, until="11:00", stop="opposite", trail_mult=1.0):
    mult,tick=SPEC[sym]; out=[]
    for day,g in m.groupby(m.index.date):
        if len(g)<300: continue
        orr=g[g.index.time<(pd.Timestamp("09:30")+pd.Timedelta(minutes=mins)).time()]
        hi,lo=orr.high.max(),orr.low.min(); w=hi-lo
        if w<=0: continue
        rest=g[g.index.time>=(pd.Timestamp("09:30")+pd.Timedelta(minutes=mins)).time()]
        o,h,l,c=(rest[k].values for k in "open high low close".split()); tt=rest.index
        cut=pd.Timestamp(until).time(); pos=0
        for i in range(len(rest)-1):
            if tt[i].time()>cut: break
            if c[i]>hi: pos=1
            elif c[i]<lo: pos=-1
            if pos:
                e=o[i+1]+pos*tick; st=(lo if pos==1 else hi) if stop=="opposite" else (hi+lo)/2
                risk=abs(e-st); best=e; x=None
                for j in range(i+1,len(rest)):
                    if (pos==1 and l[j]<=st) or (pos==-1 and h[j]>=st): x=st-pos*tick; why="stop"; break
                    best=max(best,h[j]) if pos==1 else min(best,l[j])
                    st=max(st,best-trail_mult*w) if pos==1 else min(st,best+trail_mult*w)
                    if tt[j].time()>=pd.Timestamp("15:55").time(): x=c[j]-pos*tick; why="eod"; break
                if x is None: x=c[-1]-pos*tick; why="eod"
                pnl=pos*(x-e)*mult-0.5; out.append((tt[i],pos,e,x,pnl,pnl/(risk*mult) if risk else 0,why)); break
    return pd.DataFrame(out,columns="entry side ep xp pnl R why".split())
def vwap_fade(sym, m, k=2.0, start="10:00"):
    mult,tick=SPEC[sym]; out=[]
    for day,g in m.groupby(m.index.date):
        if len(g)<300: continue
        tp=(g.high+g.low+g.close)/3; v=g.volume.clip(lower=1)
        vw=(tp*v).cumsum()/v.cumsum(); var=((tp-vw)**2*v).cumsum()/v.cumsum(); sd=np.sqrt(var)
        o,h,l,c=(g[k_].values for k_ in "open high low close".split()); tt=g.index; VW=vw.values; SD=sd.values
        taken=0
        i=1
        while i<len(g)-1 and taken<3:
            if tt[i].time()<pd.Timestamp(start).time() or tt[i].time()>=pd.Timestamp("15:30").time(): i+=1; continue
            up=h[i-1]>VW[i-1]+k*SD[i-1] and c[i]<l[i-1]          # stretched above, then reversal bar
            dn=l[i-1]<VW[i-1]-k*SD[i-1] and c[i]>h[i-1]
            if up or dn:
                pos=-1 if up else 1; e=o[i+1]+pos*tick
                st=(max(h[i-1],h[i])+tick) if pos==-1 else (min(l[i-1],l[i])-tick); risk=abs(e-st); x=None
                for j in range(i+1,len(g)):
                    if (pos==1 and l[j]<=st) or (pos==-1 and h[j]>=st): x=st-pos*tick; why="stop"; break
                    if (pos==1 and h[j]>=VW[j]) or (pos==-1 and l[j]<=VW[j]): x=VW[j]-pos*tick; why="vwap"; break
                    if tt[j].time()>=pd.Timestamp("15:55").time(): x=c[j]-pos*tick; why="eod"; break
                if x is None: x=c[-1]; why="eod"
                pnl=pos*(x-e)*mult-0.5; out.append((tt[i],pos,e,x,pnl,pnl/(risk*mult) if risk else 0,why)); taken+=1
                i=j+1; continue
            i+=1
    return pd.DataFrame(out,columns="entry side ep xp pnl R why".split())
if __name__=="__main__":
    for s in ["MNQ","MYM"]:
        m=load1(s); mid=m.index[0]+(m.index[-1]-m.index[0])/2
        tests={f"ORB {mn}m, stop other side":(lambda mn=mn: orb(s,m,mn)) for mn in (5,15)}
        tests.update({f"ORB {mn}m, stop mid":(lambda mn=mn: orb(s,m,mn,stop="mid")) for mn in (5,15)})
        tests.update({f"VWAP fade {k} sd":(lambda k=k: vwap_fade(s,m,k)) for k in (2.0,2.5,3.0)})
        for nm,f in tests.items():
            t=f(); a,b=t[t.entry<mid],t[t.entry>=mid]
            print(s,f"{nm:24s} H1 {a.R.mean():+.2f}R ${a.pnl.sum():>7,.0f} | H2 {b.R.mean():+.2f}R ${b.pnl.sum():>7,.0f} | n{len(t)} win {(t.pnl>0).mean():.0%}",flush=True)

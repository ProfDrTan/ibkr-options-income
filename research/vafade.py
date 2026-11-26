"""Gold value-area fade: previous CME session's VAH/VAL/POC (from 1-min volume); during London (08:00-10:00 London)
and New York (08:20-10:20 NY) opens, price pokes beyond VAH/VAL and the 5-min candle closes back inside (rejection)
-> fade toward POC. Stop beyond the rejection candle +1 tick. Exit at POC, or after 4 hours."""
import pandas as pd, numpy as np
from engine import SPEC
H="/home/claude/ibkr-options-income/data/history/"
def load(sym):
    m=pd.read_csv(H+f"{sym}_1m.csv.gz",parse_dates=["time_utc"]).set_index("time_utc").sort_index()
    return m[~m.index.duplicated()]
def session_profiles(m, tick, va=0.70, binticks=5):
    et=m.index.tz_convert("America/New_York"); sess=(et+pd.Timedelta(hours=6)).date   # session starts 18:00 ET
    prof={}
    for s,g in m.groupby(sess):
        if len(g)<300: continue
        tp=((g.high+g.low+g.close)/3/(tick*binticks)).round()*tick*binticks
        vol=g.volume.groupby(tp).sum().sort_index()
        prices=vol.index.values; v=vol.values; p=int(v.argmax()); a=b=p; tot=v[p]
        while tot<va*v.sum():
            up=v[b+1] if b+1<len(v) else -1; dn=v[a-1] if a>0 else -1
            if up>=dn: b+=1; tot+=up
            else: a-=1; tot+=dn
        prof[s]=(prices[p],prices[b],prices[a])
    return pd.DataFrame(prof,index=["poc","vah","val"]).T, sess
def run(sym, windows=("london","ny"), target="poc", hold_h=4):
    mult,tick=SPEC[sym]; m=load(sym); prof,sess=session_profiles(m,tick)
    m5=m.resample("5min").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
    et5=m5.index.tz_convert("America/New_York"); ld5=m5.index.tz_convert("Europe/London")
    s5=(et5+pd.Timedelta(hours=6)).date
    days=sorted(prof.index); prev={d:days[i-1] for i,d in enumerate(days) if i>0}
    out=[]; o,h,l,c=(m5[k].values for k in "open high low close".split()); t=m5.index
    i=1; mins_ld=ld5.hour*60+ld5.minute; mins_ny=et5.hour*60+et5.minute
    last_day=None
    while i<len(m5)-1:
        inwin=(("london" in windows) and 480<=mins_ld[i]<600) or (("ny" in windows) and 500<=mins_ny[i]<620)
        pd_=prev.get(s5[i])
        if not inwin or pd_ is None: i+=1; continue
        poc,vah,val=prof.loc[pd_]
        short=h[i]>vah and c[i]<vah and o[i]<=vah+(h[i]-vah)
        long_=l[i]<val and c[i]>val
        if (short and poc<c[i]) or (long_ and poc>c[i]):
            pos=-1 if short else 1; e=o[i+1]+pos*tick; st=(h[i]+tick) if pos==-1 else (l[i]-tick); risk=abs(e-st)
            if risk<=0: i+=1; continue
            x=None; end=t[i]+pd.Timedelta(hours=hold_h)
            for j in range(i+1,len(m5)):
                if (pos==1 and l[j]<=st) or (pos==-1 and h[j]>=st): x=st-pos*tick; why="stop"; break
                if target=="poc" and ((pos==1 and h[j]>=poc) or (pos==-1 and l[j]<=poc)): x=poc-pos*tick; why="poc"; break
                if t[j]>=end: x=c[j]-pos*tick; why="time"; break
            if x is None: break
            pnl=pos*(x-e)*mult-0.5; out.append((t[i],pos,e,x,pnl,pnl/(risk*mult),why)); i=j+1; continue
        i+=1
    return pd.DataFrame(out,columns="entry side ep xp pnl R why".split())
if __name__=="__main__":
    for sym in ["MGC"]:
        for w in [("london","ny"),("london",),("ny",)]:
            for tgt in ["poc","none"]:
                tr=run(sym,w,tgt); mid=tr.entry.min()+(tr.entry.max()-tr.entry.min())/2
                a,b=tr[tr.entry<mid],tr[tr.entry>=mid]
                print(sym,"+".join(w),f"exit={tgt:4s}",f"H1 {a.R.mean():+.2f}R ${a.pnl.sum():>6,.0f} | H2 {b.R.mean():+.2f}R ${b.pnl.sum():>6,.0f} | n{len(tr)} win {(tr.pnl>0).mean():.0%}",flush=True)

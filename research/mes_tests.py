from intraday import load1, orb, vwap_fade
from failbo import run as fb
for s in ["MES","MNQ"]:
    m=load1(s); mid=m.index[0]+(m.index[-1]-m.index[0])/2
    T={"ORB 5m":lambda: orb(s,m,5),"ORB 15m":lambda: orb(s,m,15),"VWAP fade 2.5sd":lambda: vwap_fade(s,m,2.5),
       "failed breakout fade, exit VWAP":lambda: fb(s,"vwap"),"failed breakout fade, trail":lambda: fb(s,"trail")}
    if s=="MNQ": T={k:v for k,v in T.items() if "failed" in k}
    for nm,f in T.items():
        t=f(); a,b=t[t.entry<mid],t[t.entry>=mid]
        print(s,f"{nm:32s} H1 {a.R.mean():+.2f}R ${a.pnl.sum():>7,.0f} | H2 {b.R.mean():+.2f}R ${b.pnl.sum():>7,.0f} | n{len(t)} win {(t.pnl>0).mean():.0%}",flush=True)

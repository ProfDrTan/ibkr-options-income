import pandas as pd, numpy as np
from engine import *; from strategies import *; from cont import D, day_break
def disp(d,k=3.0,look=4,n=32):
    a=atr(d).shift(look); mv=d.close-d.close.shift(look)
    up=(mv>k*a)&(d.close>d.high.rolling(n).max().shift()); dn=(mv<-k*a)&(d.close<d.low.rolling(n).min().shift())
    return pd.Series(np.where(up,1,np.where(dn,-1,0)),d.index)
def base(d,s):
    k={"MGC":2,"MNQ":2,"MYM":4}[s]; sq=squeeze(d,40,.2); b=donchian(d,96); b=b.where(b==htf_trend(d,k=k),0)
    return sq.where(sq!=0,b)
def evaluate(name,f):
    out=[name]; tot=0
    for s,d in D.items():
        t=backtest(d,f(d,s),s); mid=d.index[0]+(d.index[-1]-d.index[0])/2
        dc=d.close.resample("1D").last().dropna().pct_change().dropna(); big=dc.abs().sort_values(ascending=False).head(15).index
        e=t.entry.dt.floor("1D"); x=t.exit.dt.floor("1D"); hit=[((e<=b)&(x>=b)) for b in big]
        out.append(f"{s} {t[t.entry<mid].R.mean():+.2f}/{t[t.entry>=mid].R.mean():+.2f} big{sum(h.any() for h in hit)} ${sum(t[h].pnl.sum() for h in hit):,.0f}"); tot+=t.pnl.sum()
    out.append(f"tot ${tot:,.0f}"); print(" | ".join(out),flush=True)
if __name__=="__main__":
    evaluate("current",base)
    for k in [2.5,3,4]:
        for look in [2,4,8]:
            evaluate(f"+disp k{k} l{look}",lambda d,s,k=k,l=look:(lambda b,x:b.where(b!=0,x))(base(d,s),disp(d,k,l)))
    evaluate("+db96 no trend",lambda d,s:(lambda b,x:b.where(b!=0,x))(base(d,s),donchian(d,96)))

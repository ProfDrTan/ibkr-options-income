"""Time-series momentum (Moskowitz, Ooi & Pedersen 2012 style) on ETFs, daily data, 20 years.
Signal per ETF: sign of past return (blend of 1, 3, 12 months). Size: inverse volatility so each holding targets the same
risk; whole book scaled to a target volatility. Rebalance weekly (Friday close, trade next open approximated by close).
Costs: 5 bp per unit of turnover."""
import pandas as pd, numpy as np
H="/home/claude/ibkr-options-income/data/history/daily/"
UNIV=["SPY","QQQ","IWM","EFA","EEM","TLT","IEF","SHY","HYG","LQD","GLD","SLV","USO","UUP","VNQ"]
def load():
    px={}
    for s in UNIV:
        d=pd.read_csv(H+f"{s}.csv",parse_dates=["date"]).set_index("date").close; px[s]=d
    return pd.DataFrame(px).sort_index()
def run(px, lookbacks=(21,63,252), mode="longshort", tvol=0.10, rebal="W-FRI", cost=0.0005, lev_cap=2.0):
    r=px.pct_change()
    sig=sum(np.sign(px/px.shift(lb)-1) for lb in lookbacks)/len(lookbacks)
    if mode=="longflat": sig=sig.clip(lower=0)
    vol=r.ewm(span=60).std()*np.sqrt(252)
    raw=(sig/vol).replace([np.inf,-np.inf],np.nan)
    n=raw.notna().sum(axis=1).clip(lower=1)
    w=raw.div(n,axis=0)*tvol                       # each asset ~ tvol/n risk before diversification
    # scale the book to target vol using trailing realised portfolio vol
    pr=(w.shift(1)*r).sum(axis=1)
    book=pr.ewm(span=60).std()*np.sqrt(252)
    scale=(tvol/book).clip(upper=lev_cap).shift(1)
    w=w.mul(scale,axis=0)
    gross=w.abs().sum(axis=1); w=w.div((gross/lev_cap).clip(lower=1),axis=0)
    w=w.resample(rebal).last().reindex(px.index).ffill()
    ret=(w.shift(1)*r).sum(axis=1)-cost*w.diff().abs().sum(axis=1)
    return ret.loc["2007-01-01":], w
def stats(ret):
    eq=(1+ret).cumprod(); yrs=len(ret)/252
    cagr=eq.iloc[-1]**(1/yrs)-1; vol=ret.std()*np.sqrt(252); dd=(eq/eq.cummax()-1).min()
    return dict(CAGR=f"{cagr:.1%}",vol=f"{vol:.1%}",Sharpe=round(ret.mean()/ret.std()*np.sqrt(252),2),maxDD=f"{dd:.1%}")
if __name__=="__main__":
    px=load(); print("data", px.index[0].date(), px.index[-1].date(), px.notna().sum().min(),"rows min")
    spy=px.SPY.pct_change().loc["2007-01-01":]
    res={"SPY buy & hold":spy}
    for mode in ("longshort","longflat"):
        for lb in ((252,),(21,63,252)):
            ret,_=run(px,lb,mode); res[f"TSMOM {mode} lb{lb}"]=ret
    yrs=[2008,2009,2011,2015,2018,2020,2022,2023,2024,2025]
    for k,v in res.items():
        a=v.loc[:"2016-06-30"]; b=v.loc["2016-07-01":]
        yr={y:f"{(1+v.loc[str(y)]).prod()-1:+.0%}" for y in yrs if str(y) in v.index.year.astype(str)}
        print(f"{k:32s} all {stats(v)} | 1st half Sharpe {stats(a)['Sharpe']} | 2nd half Sharpe {stats(b)['Sharpe']}")
        print("   ", yr)

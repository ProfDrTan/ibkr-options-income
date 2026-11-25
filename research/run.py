import sys, pandas as pd, itertools
from engine import *; from strategies import *
H="/home/claude/ibkr-options-income/data/history/"
CANDS = {
 "donchian20": lambda d: donchian(d,20),
 "donchian40": lambda d: donchian(d,40),
 "donch20+trend": lambda d: donchian_trend(d,20),
 "donch40+trend": lambda d: donchian_trend(d,40),
 "donch20+trend+ER.2": lambda d: donchian_trend(d,20,0.2),
 "donch40+trend+ER.2": lambda d: donchian_trend(d,40,0.2),
 "squeeze20+trend": lambda d: squeeze(d,20),
 "squeeze20": lambda d: squeeze(d,20,trend=False),
 "pullback_rsi2": lambda d: pullback(d),
 "orb_ny+trend": lambda d: orb(d,"ny"),
 "orb_ldn+trend": lambda d: orb(d,"ldn"),
 "orb_asia+trend": lambda d: orb(d,"asia"),
 "orb_ny": lambda d: orb(d,"ny",trend=False),
 "orb_ldn": lambda d: orb(d,"ldn",trend=False),
}
def run(sym):
    df=load(sym,H+f"{sym}_15m.csv.gz"); mid=df.index[0]+(df.index[-1]-df.index[0])/2
    rows=[]
    for name,f in CANDS.items():
        tr=backtest(df,f(df),sym)
        a=stats(tr[tr.entry<mid]); b=stats(tr[tr.entry>=mid])
        rows.append(dict(strat=name, IS_n=a["n"],IS_R=a["R"],IS_pf=a["pf"], OOS_n=b["n"],OOS_R=b["R"],OOS_pf=b["pf"],OOS_usd=b.get("usd",0),OOS_dd=b.get("ddR",0)))
    return pd.DataFrame(rows)
if __name__=="__main__":
    pd.set_option("display.width",200)
    for s in sys.argv[1:]: print(s); print(run(s).to_string(index=False))

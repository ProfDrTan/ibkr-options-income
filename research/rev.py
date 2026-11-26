import pandas as pd, numpy as np
from engine import *; from strategies import *
from bigrule import base, evaluate
def rev(d,K=15,N=8,look=96):
    a=atr(d)
    dropped=((d.high.rolling(look).max()-d.low.rolling(N*2).min())/a>K)   # big fall within last day, low recent
    rallied=((d.high.rolling(N*2).max()-d.low.rolling(look).min())/a>K)
    up=dropped.shift()&(d.close>d.high.rolling(N).max().shift()); dn=rallied.shift()&(d.close<d.low.rolling(N).min().shift())
    s=pd.Series(np.where(up,1,np.where(dn,-1,0)),d.index)
    return s
if __name__=="__main__":
    evaluate("current",base)
    for K in [12,16,20,25]:
        for N in [8,16]:
            evaluate(f"+rev K{K} N{N}",lambda d,s,K=K,N=N:(lambda b,x:b.where(b!=0,x))(base(d,s),rev(d,K,N)))

def rev2(d,K=15,N=8,look=96,win=16):
    a=atr(d).rolling(192).median()          # slow ATR: a crash doesn't shrink its own yardstick
    dropped=((d.high.rolling(look).max()-d.low.rolling(win).min())/a>K)
    rallied=((d.high.rolling(win).max()-d.low.rolling(look).min())/a>K)
    up=dropped.shift()&(d.close>d.high.rolling(N).max().shift()); dn=rallied.shift()&(d.close<d.low.rolling(N).min().shift())
    return pd.Series(np.where(up,1,np.where(dn,-1,0)),d.index)

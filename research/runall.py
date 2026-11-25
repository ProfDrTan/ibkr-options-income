import sys, run, pandas as pd
from strategies import *
run.CANDS.update({
 "squeeze40+trend": lambda d: squeeze(d,40),
 "squeeze+vol": lambda d: squeeze_vol(d),
 "volbreak20+trend": lambda d: vol_breakout(d),
 "volbreak40+trend": lambda d: vol_breakout(d,40),
 "nr7+trend": lambda d: nr7(d),
})
pd.set_option("display.width",220)
for s in sys.argv[1:]:
    print(s); print(run.run(s).to_string(index=False))

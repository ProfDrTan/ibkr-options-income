"""
discipline_bot/charts.py: pictures for Telegram (sent with /status and /summary, or /chart on its own).

market_chart(): one panel per market (gold, Nasdaq, Dow), last 24 hours of 15-minute candles, SGT time axis,
  each level drawn as a labelled line: 24h high/low, Fibonacci 38.2/50/61.8 of the 24h swing, entry and stop
  of every open lot; entry/exit arrows for trades closed in the window.
performance_chart(): cumulative closed P&L over time per market (new rules only), plus P&L per market bars.
matplotlib is installed on first use if missing.
"""
import csv, datetime as dt, io, subprocess, sys

SGT = dt.timezone(dt.timedelta(hours=8))
MCOL = {"MGC": "#D4A017", "MNQ": "#1F6FEB", "MYM": "#2DA44E"}
MNAME = {"MGC": "Gold (MGC)", "MNQ": "Nasdaq (MNQ)", "MYM": "Dow (MYM)"}


def _plt():
    try:
        import matplotlib
    except ImportError:
        import importlib, site
        log = []
        for extra in ([], ["--user"]):
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *extra, "matplotlib"],
                               capture_output=True, text=True)
            log.append(f"pip {' '.join(extra) or 'system'} rc={r.returncode} {r.stderr[-300:]}")
            if r.returncode == 0:
                break
        for d in [site.getusersitepackages()] + list(getattr(site, "getsitepackages", lambda: [])()):
            site.addsitedir(d)                       # a fresh --user install is not on sys.path until we add it
        importlib.invalidate_caches()
        try:
            import matplotlib
        except ImportError:
            raise ImportError("matplotlib install failed: " + " | ".join(log))
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    return plt, mdates


def _tags(ax, items, lo, hi):
    """Price tags on the right edge, pushed apart so they never overlap. items: (y, text, color, style)."""
    span = hi - lo
    gap = span * 0.055
    inside = sorted([it for it in items if lo <= it[0] <= hi], key=lambda it: it[0])
    pos = []
    for it in inside:
        y = it[0] if not pos or it[0] - pos[-1] >= gap else pos[-1] + gap
        pos.append(y)
    over = pos[-1] - hi + gap * 0.3 if pos else 0           # pushed past the top: shift the stack down
    if over > 0:
        pos = [p - over for p in pos]
        for i in range(len(pos) - 2, -1, -1):
            if pos[i + 1] - pos[i] < gap:
                pos[i] = pos[i + 1] - gap
    for (y, text, color, ls), yp in zip(inside, pos):
        ax.axhline(y, color=color, lw=1.4 if ls == "-" else 1.1, ls=ls, alpha=0.9)
        ax.annotate(f"{text}  {y:,.1f}", xy=(1.0, y), xycoords=("axes fraction", "data"), xytext=(1.01, yp),
                    textcoords=("axes fraction", "data"), fontsize=11, color="white", va="center", ha="left",
                    fontweight="bold", bbox=dict(boxstyle="round,pad=0.25", fc=color, ec="none"),
                    arrowprops=dict(arrowstyle="-", color=color, lw=0.8) if abs(yp - y) > gap * 0.2 else None,
                    annotation_clip=False)
    above = sorted([it for it in items if it[0] > hi], key=lambda it: it[0])
    below = sorted([it for it in items if it[0] < lo], key=lambda it: -it[0])
    for k, (y, text, color, ls) in enumerate(above):           # off-screen levels: listed at the edge, stacked
        ax.annotate(f"▲ {text} {y:,.1f}", xy=(0.01, 0.97 - 0.045 * k), xycoords="axes fraction", fontsize=10,
                    color=color, va="top", bbox=dict(fc="white", ec="none", alpha=0.8, pad=0.5))
    for k, (y, text, color, ls) in enumerate(below):
        ax.annotate(f"▼ {text} {y:,.1f}", xy=(0.01, 0.03 + 0.045 * k), xycoords="axes fraction", fontsize=10,
                    color=color, va="bottom", bbox=dict(fc="white", ec="none", alpha=0.8, pad=0.5))


def market_chart(bars_by_sym, lots_by_sym, trades_rows, hours=12, open_pnl=None):
    """One PNG per market, phone-sized. Returns {sym: png bytes}."""
    plt, mdates = _plt()
    out = {}
    for sym in [s for s in MCOL if bars_by_sym.get(s)]:
        allbars = bars_by_sym[sym]
        day = allbars[-96:]
        bars = allbars[-hours * 4:]
        col = MCOL[sym]
        fig, ax = plt.subplots(figsize=(7.5, 7.5), dpi=160)
        t = [b.date.astimezone(SGT) for b in bars]
        w = dt.timedelta(minutes=10)
        for b, x in zip(bars, t):
            c = "#2DA44E" if b.close >= b.open else "#CF222E"
            ax.plot([x, x], [b.low, b.high], color=c, lw=1.2)
            ax.add_patch(plt.Rectangle((mdates.date2num(x - w / 2), min(b.open, b.close)),
                                       mdates.date2num(x + w / 2) - mdates.date2num(x - w / 2),
                                       max(abs(b.close - b.open), 1e-9), color=c))
        lo, hi = min(b.low for b in bars), max(b.high for b in bars)
        items = []
        lots = lots_by_sym.get(sym, [])
        for lot in lots:
            lc = "#2DA44E" if lot["side"] == "BUY" else "#CF222E"
            items.append((float(lot["entry_price"]), f"L{lot.get('lot', 1)} {'LONG' if lot['side'] == 'BUY' else 'SHORT'}", lc, "-"))
            if lot.get("stop_now"):
                items.append((float(lot["stop_now"]), f"L{lot.get('lot', 1)} stop", "#8B0000", ":"))
        lo = min([lo] + [it[0] for it in items]); hi = max([hi] + [it[0] for it in items])
        pad = (hi - lo) * 0.06
        lo, hi = lo - pad, hi + pad
        dh, dl = max(b.high for b in day), min(b.low for b in day)
        items += [(dh, "24h high", "#57606A", "--"), (dl, "24h low", "#57606A", "--")]
        upsw = day.index(max(day, key=lambda b: b.high)) > day.index(min(day, key=lambda b: b.low))
        for f in (0.382, 0.5, 0.618):
            items.append((dh - f * (dh - dl) if upsw else dl + f * (dh - dl), f"Fib {f * 100:.1f}%", "#8250DF", "--"))
        last = bars[-1].close
        items.append((last, "NOW", col, "-"))
        ax.set_ylim(lo, hi)
        _tags(ax, items, lo, hi)
        for r in trades_rows:
            if r.get("symbol") != sym:
                continue
            try:
                et = dt.datetime.fromisoformat(r["entry_time"]).astimezone(SGT)
                xt = dt.datetime.fromisoformat(r["exit_time"]).astimezone(SGT)
                pnl = float(r["pnl_usd"] or 0)
            except Exception:
                continue
            if et >= t[0]:
                ax.annotate("▲" if r["side"] == "BUY" else "▼", (et, float(r["entry_price"])), color="#1F6FEB",
                            fontsize=16, ha="center", va="center")
            if xt >= t[0]:
                ax.annotate(f"{'+' if pnl >= 0 else '−'}${abs(pnl):,.0f}", (xt, float(r["exit_price"])), fontsize=11,
                            fontweight="bold", color="#2DA44E" if pnl >= 0 else "#CF222E", ha="center", va="bottom")
        head = f"{MNAME[sym]}  {last:,.2f}"
        if lots:
            u = (open_pnl or {}).get(sym)
            banner = (f"OPEN POSITION: {len(lots)} lot{'s' if len(lots) > 1 else ''} "
                      f"{'LONG' if lots[0]['side'] == 'BUY' else 'SHORT'}"
                      + (f"  ·  open P&L {'+' if u >= 0 else '−'}${abs(u):,.0f}" if u is not None else ""))
            bcol = "#2DA44E" if (u or 0) >= 0 else "#CF222E"
        else:
            banner, bcol = "NO OPEN POSITION  ·  watching for a signal", "#57606A"
        fig.text(0.97, 0.965, banner, color="white", fontsize=12, fontweight="bold", ha="right", va="top",
                 bbox=dict(boxstyle="round,pad=0.4", fc=bcol, ec="none"))
        fig.text(0.03, 0.905, head, color=col, fontsize=16, fontweight="bold", va="top")
        fig.text(0.03, 0.87, f"{dt.datetime.now(SGT):%a %d %b %H:%M} SGT · 15-min candles · last {hours}h",
                 fontsize=10, color="#57606A", va="top")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=SGT))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2, tz=SGT))
        ax.tick_params(axis="x", labelsize=11)
        ax.yaxis.tick_right(); ax.tick_params(axis="y", labelsize=11, labelright=False)   # tags carry the prices
        ax.grid(alpha=0.25)
        for sp in ("top", "left", "right"):
            ax.spines[sp].set_visible(False)
        fig.subplots_adjust(left=0.03, right=0.70, top=0.83, bottom=0.07)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        out[sym] = buf.getvalue()
    return out


def performance_chart(trades_rows, is_new):
    plt, mdates = _plt()
    rows = [r for r in trades_rows if is_new(r) and r.get("pnl_usd") not in ("", "nan", None)]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 8), dpi=110, gridspec_kw={"height_ratios": [2, 1]})
    if not rows:
        a1.text(0.5, 0.5, "No closed trades yet under the new rules\n(open positions: see /status)", ha="center", va="center", fontsize=13)
        a1.axis("off"); a2.axis("off")
    else:
        rows.sort(key=lambda r: r["exit_time"])
        tot, xs, ys = 0.0, [], []
        for r in rows:
            tot += float(r["pnl_usd"]); xs.append(dt.datetime.fromisoformat(r["exit_time"]).astimezone(SGT)); ys.append(tot)
        a1.step(xs, ys, where="post", color="#24292F", lw=2, label="All markets")
        for sym, col in MCOL.items():
            s, px, py = 0.0, [], []
            for r in rows:
                if r["symbol"] == sym:
                    s += float(r["pnl_usd"]); px.append(dt.datetime.fromisoformat(r["exit_time"]).astimezone(SGT)); py.append(s)
            if px:
                a1.step(px, py, where="post", color=col, lw=1.5)
                a1.text(px[-1], py[-1], f" {sym} {'+' if py[-1] >= 0 else '−'}${abs(py[-1]):,.0f}", color=col, fontsize=9, va="center")
        a1.text(xs[-1], ys[-1], f" Total {'+' if ys[-1] >= 0 else '−'}${abs(ys[-1]):,.0f}", fontsize=10, fontweight="bold", va="bottom")
        a1.axhline(0, color="#999999", lw=0.8)
        a1.set_title("CLOSED TRADES ONLY: running total of realised profit/loss\n(open positions are not in here; see /status)", loc="left", fontsize=12, fontweight="bold")
        a1.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M", tz=SGT))
        a1.tick_params(axis="x", labelsize=8)
        a1.grid(alpha=0.2)
        names, vals, cols, wins = [], [], [], []
        for sym, col in MCOL.items():
            rs = [r for r in rows if r["symbol"] == sym]
            names.append(sym); cols.append(col)
            vals.append(sum(float(r["pnl_usd"]) for r in rs))
            wins.append(f"{sum(1 for r in rs if float(r['pnl_usd']) > 0)}/{len(rs)} won")
        bars = a2.bar(names, vals, color=cols)
        for b, v, wtxt in zip(bars, vals, wins):
            a2.text(b.get_x() + b.get_width() / 2, v, f"{'+' if v >= 0 else '−'}${abs(v):,.0f}\n{wtxt}", ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=9)
        lo_, hi_ = min(vals + [0]), max(vals + [0]); pad = (hi_ - lo_ or 100) * 0.35
        a2.set_ylim(lo_ - pad, hi_ + pad)
        a2.axhline(0, color="#999999", lw=0.8)
        a2.set_title("Realised profit/loss by market (closed trades)", loc="left", fontsize=12, fontweight="bold")
        for a in (a1, a2):
            a.yaxis.tick_right(); a.yaxis.set_label_position("right")
            for s in ("top", "left"):
                a.spines[s].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def read_trades(path):
    try:
        return list(csv.DictReader(open(path)))
    except FileNotFoundError:
        return []

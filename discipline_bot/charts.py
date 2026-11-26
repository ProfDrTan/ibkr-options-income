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
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", "matplotlib"], check=False)
        import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    return plt, mdates


def _label(ax, y, text, color, x):
    ax.axhline(y, color=color, lw=1, ls="--", alpha=0.8)
    ax.text(x, y, f" {text} · {y:,.1f}", color=color, fontsize=8, va="bottom", ha="left",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=0.5))


def market_chart(bars_by_sym, lots_by_sym, trades_rows):
    """bars_by_sym: {sym: [bar...]} completed 15m bars (ib_async BarData). Returns PNG bytes."""
    plt, mdates = _plt()
    syms = [s for s in MCOL if bars_by_sym.get(s)]
    fig, axes = plt.subplots(len(syms), 1, figsize=(8, 4.2 * len(syms)), dpi=110)
    axes = axes if len(syms) > 1 else [axes]
    for ax, sym in zip(axes, syms):
        bars = bars_by_sym[sym][-96:]
        col = MCOL[sym]
        t = [b.date.astimezone(SGT) for b in bars]
        w = dt.timedelta(minutes=10)
        for b, x in zip(bars, t):
            up = b.close >= b.open
            c = "#2DA44E" if up else "#CF222E"
            ax.plot([x, x], [b.low, b.high], color=c, lw=0.8)
            ax.add_patch(plt.Rectangle((mdates.date2num(x - w / 2), min(b.open, b.close)), mdates.date2num(x + w / 2) - mdates.date2num(x - w / 2),
                                       max(abs(b.close - b.open), 1e-9), color=c))
        hi, lo = max(b.high for b in bars), min(b.low for b in bars)
        xl = t[0]
        _label(ax, hi, "24h high", "#555555", xl)
        _label(ax, lo, "24h low", "#555555", xl)
        up_swing = bars.index(max(bars, key=lambda b: b.high)) > bars.index(min(bars, key=lambda b: b.low))
        for f in (0.382, 0.5, 0.618):
            y = hi - f * (hi - lo) if up_swing else lo + f * (hi - lo)
            _label(ax, y, f"Fib {f * 100:.1f}%", "#8250DF", t[len(t) // 3])
        for lot in lots_by_sym.get(sym, []):
            lc = "#2DA44E" if lot["side"] == "BUY" else "#CF222E"
            _label(ax, float(lot["entry_price"]), f"lot {lot.get('lot', 1)} {'LONG' if lot['side'] == 'BUY' else 'SHORT'} entry", lc, t[(2 * len(t)) // 3])
            if lot.get("stop_now"):
                _label(ax, float(lot["stop_now"]), f"lot {lot.get('lot', 1)} stop", "#CF222E", t[(2 * len(t)) // 3])
        for r in trades_rows:
            if r.get("symbol") != sym:
                continue
            try:
                et = dt.datetime.fromisoformat(r["entry_time"]).astimezone(SGT)
                xt = dt.datetime.fromisoformat(r["exit_time"]).astimezone(SGT)
            except Exception:
                continue
            if et >= t[0]:
                ax.annotate("▲" if r["side"] == "BUY" else "▼", (et, float(r["entry_price"])), color="#1F6FEB",
                            fontsize=12, ha="center", va="center")
            if xt >= t[0]:
                pnl = float(r["pnl_usd"] or 0)
                ax.annotate(f"exit {'+' if pnl >= 0 else '−'}${abs(pnl):,.0f}", (xt, float(r["exit_price"])), fontsize=8,
                            color="#2DA44E" if pnl >= 0 else "#CF222E", ha="center", va="bottom")
        ax.set_title(f"{MNAME[sym]}  ·  last {bars[-1].close:,.2f}", color=col, fontsize=12, fontweight="bold", loc="left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %H:%M", tz=SGT))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=3, tz=SGT))
        ax.tick_params(axis="x", labelsize=8, rotation=0)
        ax.grid(alpha=0.2)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.suptitle(f"Discipline Bot · {dt.datetime.now(SGT):%a %d %b %H:%M} SGT · 15-min bars", fontsize=11)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def performance_chart(trades_rows, is_new):
    plt, mdates = _plt()
    rows = [r for r in trades_rows if is_new(r) and r.get("pnl_usd") not in ("", "nan", None)]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 8), dpi=110, gridspec_kw={"height_ratios": [2, 1]})
    if not rows:
        a1.text(0.5, 0.5, "No closed trades yet under the new rules", ha="center", va="center", fontsize=13)
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
        a1.set_title("Closed-trade P&L, cumulative (new rules, 1 contract per lot)", loc="left", fontsize=11)
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
        a2.set_title("P&L by market", loc="left", fontsize=11)
        for a in (a1, a2):
            for s in ("top", "right"):
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

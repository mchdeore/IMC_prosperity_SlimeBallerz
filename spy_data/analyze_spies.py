"""
Comprehensive analysis of all spy run data.
Parses activitiesLog + graphLog from each run JSON and extracts
microstructure insights for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.
"""

import json
import io
import os
import statistics

BASE = os.path.dirname(os.path.abspath(__file__))

RUNS = {
    "observations": ("observations/run_166532.json", 0.0),
    "orderbook":    ("orderbook/run_166756.json",    0.0),
    "signals":      ("signals/run_166877.json",      0.0),
    "trades":       ("trades/run_166949.json",        124.38),
    "experiment_ipr": ("experiment_ipr/run_167373.json", 327.41),
    "experiment_aco": ("experiment_aco/run_167459.json", 267.47),
}

def parse_activities(raw_csv: str):
    """Parse semicolon-delimited activitiesLog into list of dicts."""
    rows = []
    lines = raw_csv.strip().split("\n")
    header = [h.strip() for h in lines[0].split(";")]
    for line in lines[1:]:
        vals = [v.strip() for v in line.split(";")]
        if len(vals) != len(header):
            continue
        row = {}
        for h, v in zip(header, vals):
            try:
                row[h] = float(v)
            except ValueError:
                row[h] = v
        rows.append(row)
    return rows

def parse_pnl(raw_csv: str):
    """Parse graphLog into list of (timestamp, pnl)."""
    points = []
    for line in raw_csv.strip().split("\n"):
        parts = line.split(";")
        if len(parts) == 2:
            try:
                points.append((int(parts[0].strip()), float(parts[1].strip())))
            except ValueError:
                continue
    return points


def analyze_product(rows, product):
    """Compute microstructure stats for one product from activitiesLog rows."""
    prod_rows = [r for r in rows if r.get("product") == product]
    if not prod_rows:
        return None

    mids = []
    spreads = []
    bid_vols = []
    ask_vols = []
    imbalances = []
    timestamps = []

    for r in prod_rows:
        mid = r.get("mid_price")
        if mid is not None and mid != "" and float(mid) > 0:
            mids.append(float(mid))
            timestamps.append(r.get("timestamp", 0))

        b1 = r.get("bid_price_1")
        a1 = r.get("ask_price_1")
        if b1 is not None and a1 is not None:
            try:
                b1f, a1f = float(b1), float(a1)
                if b1f > 0 and a1f > 0:
                    spread = a1f - b1f
                    spreads.append(spread)
            except (ValueError, TypeError):
                pass

        bv = 0
        av = 0
        for lvl in range(1, 4):
            bv_key = f"bid_volume_{lvl}"
            av_key = f"ask_volume_{lvl}"
            if bv_key in r and r[bv_key] not in ("", None):
                bv += abs(float(r[bv_key]))
            if av_key in r and r[av_key] not in ("", None):
                av += abs(float(r[av_key]))
        if bv > 0 or av > 0:
            bid_vols.append(bv)
            ask_vols.append(av)
            total = bv + av
            if total > 0:
                imbalances.append((bv - av) / total)

    returns = []
    for i in range(1, len(mids)):
        if mids[i - 1] != 0:
            returns.append((mids[i] - mids[i - 1]) / mids[i - 1])

    lag1_ac = None
    if len(returns) >= 10:
        mean_r = statistics.mean(returns)
        numer = sum((returns[i] - mean_r) * (returns[i-1] - mean_r) for i in range(1, len(returns)))
        denom = sum((r - mean_r)**2 for r in returns)
        if denom > 0:
            lag1_ac = numer / denom

    stats = {
        "n_rows": len(prod_rows),
        "mid_start": mids[0] if mids else None,
        "mid_end": mids[-1] if mids else None,
        "mid_min": min(mids) if mids else None,
        "mid_max": max(mids) if mids else None,
        "mid_range": (max(mids) - min(mids)) if mids else None,
        "mid_mean": round(statistics.mean(mids), 2) if mids else None,
        "mid_stdev": round(statistics.stdev(mids), 4) if len(mids) > 1 else None,
        "total_drift": round(mids[-1] - mids[0], 2) if mids else None,
        "spread_mean": round(statistics.mean(spreads), 2) if spreads else None,
        "spread_min": min(spreads) if spreads else None,
        "spread_max": max(spreads) if spreads else None,
        "spread_stdev": round(statistics.stdev(spreads), 4) if len(spreads) > 1 else None,
        "bid_vol_mean": round(statistics.mean(bid_vols), 1) if bid_vols else None,
        "ask_vol_mean": round(statistics.mean(ask_vols), 1) if ask_vols else None,
        "imbalance_mean": round(statistics.mean(imbalances), 4) if imbalances else None,
        "imbalance_stdev": round(statistics.stdev(imbalances), 4) if len(imbalances) > 1 else None,
        "return_mean": round(statistics.mean(returns) * 1e4, 4) if returns else None,  # in bps
        "return_stdev": round(statistics.stdev(returns) * 1e4, 4) if len(returns) > 1 else None,  # in bps
        "volatility_annualized_bps": round(statistics.stdev(returns) * 1e4, 2) if len(returns) > 1 else None,
        "lag1_autocorrelation": round(lag1_ac, 4) if lag1_ac is not None else None,
    }

    if lag1_ac is not None:
        if lag1_ac < -0.05:
            stats["regime"] = "MEAN_REVERTING"
        elif lag1_ac > 0.05:
            stats["regime"] = "TRENDING"
        else:
            stats["regime"] = "RANDOM_WALK"

    return stats


def analyze_pnl(pnl_points, label):
    """PnL trajectory analysis."""
    if not pnl_points:
        return {}
    vals = [p[1] for p in pnl_points]
    peak = max(vals)
    trough = min(vals)
    final = vals[-1]
    max_drawdown = 0
    running_peak = vals[0]
    for v in vals:
        running_peak = max(running_peak, v)
        dd = running_peak - v
        max_drawdown = max(max_drawdown, dd)

    return {
        "final_pnl": final,
        "peak_pnl": peak,
        "trough_pnl": trough,
        "max_drawdown": round(max_drawdown, 2),
        "n_timestamps": len(pnl_points),
    }


def cross_run_consistency(all_stats):
    """Compare same-product stats across different spy runs for consistency."""
    products = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
    print("\n" + "=" * 70)
    print("CROSS-RUN CONSISTENCY CHECK")
    print("=" * 70)
    for prod in products:
        print(f"\n  {prod}:")
        mids_start = []
        mids_end = []
        spreads = []
        for run_name, stats in all_stats.items():
            if prod in stats and stats[prod] is not None:
                s = stats[prod]
                mids_start.append((run_name, s.get("mid_start")))
                mids_end.append((run_name, s.get("mid_end")))
                spreads.append((run_name, s.get("spread_mean")))
        if mids_start:
            print(f"    Mid at t=0:    {', '.join(f'{n}={v}' for n,v in mids_start)}")
            print(f"    Mid at t=end:  {', '.join(f'{n}={v}' for n,v in mids_end)}")
            print(f"    Avg spread:    {', '.join(f'{n}={v}' for n,v in spreads)}")
            start_vals = [v for _, v in mids_start if v is not None]
            if len(start_vals) > 1:
                if max(start_vals) - min(start_vals) < 5:
                    print(f"    -> Consistent across runs (range={max(start_vals)-min(start_vals):.1f})")
                else:
                    print(f"    -> DIVERGENCE across runs (range={max(start_vals)-min(start_vals):.1f})")


def main():
    all_product_stats = {}
    all_pnl = {}

    for run_name, (path, expected_profit) in RUNS.items():
        full_path = os.path.join(BASE, path)
        print(f"\n{'='*70}")
        print(f"  RUN: {run_name} ({path})")
        print(f"  Expected profit: {expected_profit}")
        print(f"{'='*70}")

        with open(full_path) as f:
            data = json.load(f)

        print(f"  Status: {data.get('status')}")
        print(f"  Profit: {data.get('profit')}")

        rows = parse_activities(data["activitiesLog"])
        pnl_points = parse_pnl(data.get("graphLog", ""))

        products = sorted(set(r.get("product") for r in rows if r.get("product")))
        print(f"  Products: {products}")
        print(f"  Timestamps: {len(set(r.get('timestamp') for r in rows))}")

        run_stats = {}
        for prod in products:
            stats = analyze_product(rows, prod)
            run_stats[prod] = stats
            if stats:
                print(f"\n  --- {prod} ---")
                print(f"    Price: {stats['mid_start']} -> {stats['mid_end']} (drift={stats['total_drift']})")
                print(f"    Range: [{stats['mid_min']}, {stats['mid_max']}] (width={stats['mid_range']})")
                print(f"    Mean mid: {stats['mid_mean']}, Stdev: {stats['mid_stdev']}")
                print(f"    Spread: mean={stats['spread_mean']}, min={stats['spread_min']}, max={stats['spread_max']}, stdev={stats['spread_stdev']}")
                print(f"    Depth (top3): bid_vol={stats['bid_vol_mean']}, ask_vol={stats['ask_vol_mean']}")
                print(f"    Imbalance: mean={stats['imbalance_mean']}, stdev={stats['imbalance_stdev']}")
                print(f"    Returns (bps): mean={stats['return_mean']}, stdev={stats['return_stdev']}")
                if stats.get('lag1_autocorrelation') is not None:
                    print(f"    Lag-1 AC: {stats['lag1_autocorrelation']}  =>  Regime: {stats.get('regime', '?')}")

        all_product_stats[run_name] = run_stats

        pnl_info = analyze_pnl(pnl_points, run_name)
        all_pnl[run_name] = pnl_info
        if pnl_info:
            print(f"\n  PnL curve: final={pnl_info['final_pnl']}, peak={pnl_info['peak_pnl']}, "
                  f"trough={pnl_info['trough_pnl']}, max_dd={pnl_info['max_drawdown']}")

    cross_run_consistency(all_product_stats)

    # ── Summary table ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY: KEY FINDINGS FOR STRATEGY DESIGN")
    print("=" * 70)

    # Pull stats from the passive signals run (no trading interference)
    sig_stats = all_product_stats.get("signals", {})
    obs_stats = all_product_stats.get("observations", {})
    book_stats = all_product_stats.get("orderbook", {})

    # Use signals run as "cleanest" since it doesn't trade
    ref = sig_stats if sig_stats else obs_stats

    for prod in ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]:
        s = ref.get(prod)
        if not s:
            continue
        print(f"\n  {prod}:")
        print(f"    Fair value anchor:  ~{s['mid_mean']}")
        print(f"    Intraday range:     {s['mid_range']} ticks")
        print(f"    Total drift:        {s['total_drift']}")
        print(f"    Avg spread:         {s['spread_mean']}")
        print(f"    Tick volatility:    {s['return_stdev']} bps")
        if s.get('regime'):
            print(f"    Regime:             {s['regime']} (lag1_ac={s['lag1_autocorrelation']})")
        print(f"    Avg book depth:     bid={s['bid_vol_mean']}, ask={s['ask_vol_mean']}")
        print(f"    Imbalance bias:     {s['imbalance_mean']}")

    # Experiment run analysis
    print("\n  --- EXPERIMENT RESULTS ---")
    for run_name in ["experiment_aco", "experiment_ipr"]:
        info = all_pnl.get(run_name, {})
        if info:
            print(f"\n  {run_name}:")
            print(f"    Final PnL:     {info['final_pnl']}")
            print(f"    Max drawdown:  {info['max_drawdown']}")
            print(f"    Peak PnL:      {info['peak_pnl']}")

    print("\n  --- STRATEGY IMPLICATIONS ---")
    aco = ref.get("ASH_COATED_OSMIUM", {})
    ipr = ref.get("INTARIAN_PEPPER_ROOT", {})

    if aco and ipr:
        print(f"\n  ACO (Ash Coated Osmium):")
        if aco.get("regime") == "MEAN_REVERTING":
            print(f"    -> MEAN-REVERTING: Market-making friendly")
            print(f"    -> Quote around {aco['mid_mean']}, spread ~{aco['spread_mean']}")
            print(f"    -> AC={aco['lag1_autocorrelation']}: price bounces back => wider quotes capture reversion")
        elif aco.get("regime") == "TRENDING":
            print(f"    -> TRENDING: Momentum strategy may work, or widen quotes")
        else:
            print(f"    -> RANDOM WALK: Standard market-making applies")

        print(f"\n  IPR (Intarian Pepper Root):")
        if ipr.get("regime") == "MEAN_REVERTING":
            print(f"    -> MEAN-REVERTING: Market-making friendly")
        elif ipr.get("regime") == "TRENDING":
            print(f"    -> TRENDING: Drift={ipr['total_drift']}, need directional bias or dynamic skew")
        else:
            print(f"    -> RANDOM WALK: Standard MM applies")
        print(f"    -> Drift of {ipr['total_drift']} over session = need to track fair value dynamically")
        print(f"    -> Spread={ipr['spread_mean']}, tighter edge needed (baseline_edge=3 from experiments)")

    # Compare experiment profits to know which product is more profitable
    print(f"\n  Profitability comparison:")
    print(f"    Trades spy (both products):  PnL={all_pnl.get('trades', {}).get('final_pnl', '?')}")
    print(f"    ACO experiment:              PnL={all_pnl.get('experiment_aco', {}).get('final_pnl', '?')}")
    print(f"    IPR experiment:              PnL={all_pnl.get('experiment_ipr', {}).get('final_pnl', '?')}")


if __name__ == "__main__":
    main()

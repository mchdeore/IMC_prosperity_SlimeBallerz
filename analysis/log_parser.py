"""
Unified Spy Log Parser
=======================
Parses Prosperity simulator log files produced by any of the four spy
algorithms and converts them into analysis-ready pandas DataFrames.

Spy prefixes handled:
  SPY_BOOK|   -> parse_orderbook_log()   -> orderbook_df
  SPY_OBS|    -> parse_observations_log() -> observations_df
  SPY_TRADE|  -> parse_trades_log()       -> trades_df
  SPY_SIG|    -> parse_signals_log()      -> signals_df

Usage
-----
    >>> from log_parser import parse_all
    >>> data = parse_all("path/to/prosperity_log.txt")
    >>> data["orderbook"].head()
    >>> data["signals"].head()

    >>> # Or parse a single spy type:
    >>> from log_parser import parse_signals_log
    >>> signals_df = parse_signals_log("path/to/log.txt")
"""

import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PREFIXES = {
    "SPY_BOOK|": "book",
    "SPY_OBS|": "obs",
    "SPY_TRADE|": "trade",
    "SPY_SIG|": "sig",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _PROJECT_ROOT / "output"


def _extract_lines(log_path: str, prefix: str) -> List[dict]:
    """Read a log file and extract JSON payloads with the given prefix."""
    path = Path(log_path)
    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith(prefix):
                json_str = line[len(prefix):]
                try:
                    records.append(json.loads(json_str))
                except json.JSONDecodeError:
                    continue
    return records


# ======================================================================
# Orderbook parser (SPY_BOOK|)
# ======================================================================

def parse_orderbook_log(log_path: str) -> pd.DataFrame:
    """
    Parse SPY_BOOK| lines into a long-form orderbook DataFrame.

    Columns: timestamp, tick, product, side, level, price, volume,
             bid_levels, ask_levels, bid_vol, ask_vol, spread,
             probe_side, probe_price
    """
    records = _extract_lines(log_path, "SPY_BOOK|")
    rows = []

    for rec in records:
        ts = rec.get("t")
        tick = rec.get("tick")
        books = rec.get("books", {})

        for product, book in books.items():
            base = {
                "timestamp": ts,
                "tick": tick,
                "product": product,
                "bid_levels": book.get("bid_levels"),
                "ask_levels": book.get("ask_levels"),
                "bid_vol": book.get("bid_vol"),
                "ask_vol": book.get("ask_vol"),
                "spread": book.get("spread"),
                "probe_side": book.get("probe", {}).get("side") if book.get("probe") else None,
                "probe_price": book.get("probe", {}).get("price") if book.get("probe") else None,
                "prev_probe_filled": book.get("prev_probe_filled"),
            }

            for i, (p, v) in enumerate(book.get("bids", [])):
                row = {**base, "side": "bid", "level": i + 1, "price": p, "volume": v}
                rows.append(row)

            for i, (p, v) in enumerate(book.get("asks", [])):
                row = {**base, "side": "ask", "level": i + 1, "price": p, "volume": v}
                rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df.sort_values(["timestamp", "product", "side", "level"]).reset_index(drop=True)


# ======================================================================
# Observations parser (SPY_OBS|)
# ======================================================================

def parse_observations_log(log_path: str) -> pd.DataFrame:
    """
    Parse SPY_OBS| lines into an observations DataFrame.

    Returns a wide-format DataFrame with one row per tick.
    Columns: timestamp, tick, then all plain obs fields, then
    per-product conversion fields prefixed with the product name.
    """
    records = _extract_lines(log_path, "SPY_OBS|")
    rows = []

    for rec in records:
        row = {
            "timestamp": rec.get("t"),
            "tick": rec.get("tick"),
        }

        # Positions
        for prod, pos in rec.get("pos", {}).items():
            row[f"pos_{prod}"] = pos

        # Plain observations
        for key, val in rec.get("plain", {}).items():
            row[f"plain_{key}"] = val

        # Conversion observations (flatten per product)
        for prod, obs in rec.get("conv", {}).items():
            for field, val in obs.items():
                row[f"conv_{prod}_{field}"] = val

        # Deltas
        for key, val in rec.get("deltas_plain", {}).items():
            row[f"delta_plain_{key}"] = val

        for prod, deltas in rec.get("deltas_conv", {}).items():
            for field, val in deltas.items():
                row[f"delta_conv_{prod}_{field}"] = val

        # Probe info
        probe = rec.get("probe")
        if probe:
            row["probe_phase"] = probe.get("phase")
            row["probe_conversions"] = probe.get("conversions")

        rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


# ======================================================================
# Trades parser (SPY_TRADE|)
# ======================================================================

def parse_trades_log(log_path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Parse SPY_TRADE| lines into two DataFrames:
      1. trades_df: all market + own trades (long form)
      2. probes_df: probe results with fill/no-fill

    trades_df columns: timestamp, tick, product, price, quantity,
                       buyer, seller, trade_ts, is_own
    probes_df columns: timestamp, product, side, price, offset, mid, filled
    """
    records = _extract_lines(log_path, "SPY_TRADE|")

    trade_rows = []
    probe_rows = []

    for rec in records:
        ts = rec.get("t")
        tick = rec.get("tick")

        # Market trades
        for product, trades in rec.get("market", {}).items():
            for t in trades:
                trade_rows.append({
                    "timestamp": ts,
                    "tick": tick,
                    "product": product,
                    "price": t["p"],
                    "quantity": t["q"],
                    "buyer": t["b"],
                    "seller": t["s"],
                    "trade_ts": t["ts"],
                    "is_own": False,
                })

        # Own trades
        for product, trades in rec.get("own", {}).items():
            for t in trades:
                trade_rows.append({
                    "timestamp": ts,
                    "tick": tick,
                    "product": product,
                    "price": t["p"],
                    "quantity": t["q"],
                    "buyer": t["b"],
                    "seller": t["s"],
                    "trade_ts": t["ts"],
                    "is_own": True,
                })

        # Probe results
        probe = rec.get("probe")
        prev_fill = rec.get("prev_fill")
        if probe:
            probe_rows.append({
                "timestamp": ts,
                "product": probe.get("product"),
                "side": probe.get("side"),
                "price": probe.get("price"),
                "offset": probe.get("offset"),
                "mid": probe.get("mid"),
                "filled": prev_fill,
            })

    trades_df = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame()
    probes_df = pd.DataFrame(probe_rows) if probe_rows else pd.DataFrame()

    if not trades_df.empty:
        trades_df = trades_df.sort_values(["timestamp", "product"]).reset_index(drop=True)
    if not probes_df.empty:
        probes_df = probes_df.sort_values("timestamp").reset_index(drop=True)

    return trades_df, probes_df


# ======================================================================
# Signals parser (SPY_SIG|)
# ======================================================================

def parse_signals_log(log_path: str) -> pd.DataFrame:
    """
    Parse SPY_SIG| lines into a signals DataFrame.

    Columns: timestamp, tick, product, mid, vwmp, spread, imbalance,
             wall_mid, microprice, book_pressure, bid_depth, ask_depth,
             bid_levels, ask_levels, ema_return, volatility, lag1_ac, regime
    """
    records = _extract_lines(log_path, "SPY_SIG|")
    rows = []

    for rec in records:
        ts = rec.get("t")
        tick = rec.get("tick")

        for product, sigs in rec.get("signals", {}).items():
            if sigs.get("mid") is None:
                continue

            rolling = rec.get("rolling", {}).get(product, {})

            rows.append({
                "timestamp": ts,
                "tick": tick,
                "product": product,
                "mid": sigs.get("mid"),
                "vwmp": sigs.get("vwmp"),
                "spread": sigs.get("spread"),
                "imbalance": sigs.get("imbalance"),
                "wall_mid": sigs.get("wall_mid"),
                "microprice": sigs.get("microprice"),
                "book_pressure": sigs.get("book_pressure"),
                "bid_depth": sigs.get("bid_depth"),
                "ask_depth": sigs.get("ask_depth"),
                "bid_levels": sigs.get("bid_levels"),
                "ask_levels": sigs.get("ask_levels"),
                "ema_return": rolling.get("ema_return"),
                "volatility": rolling.get("volatility"),
                "lag1_ac": rolling.get("lag1_ac"),
                "regime": rolling.get("regime"),
            })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(["timestamp", "product"]).reset_index(drop=True)


# ======================================================================
# Convenience: parse all spy types from a single log
# ======================================================================

def parse_all(log_path: str) -> Dict[str, pd.DataFrame]:
    """
    Parse a Prosperity log file for all spy prefixes.

    Returns a dict with keys:
      - "orderbook"    -> DataFrame from SPY_BOOK| lines
      - "observations" -> DataFrame from SPY_OBS| lines
      - "trades"       -> DataFrame of all trades from SPY_TRADE| lines
      - "probes"       -> DataFrame of probe results from SPY_TRADE| lines
      - "signals"      -> DataFrame from SPY_SIG| lines
    """
    trades_df, probes_df = parse_trades_log(log_path)
    return {
        "orderbook": parse_orderbook_log(log_path),
        "observations": parse_observations_log(log_path),
        "trades": trades_df,
        "probes": probes_df,
        "signals": parse_signals_log(log_path),
    }


def _detect_spy_types(log_path: str) -> List[str]:
    """Scan a log file and return which spy prefixes are present."""
    found = []
    path = Path(log_path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            for prefix, name in PREFIXES.items():
                if line.startswith(prefix) and name not in found:
                    found.append(name)
            if len(found) == len(PREFIXES):
                break
    return found


def _make_run_dir(
    run_name: Optional[str] = None,
    output_dir: Optional[str] = None,
    spy_types: Optional[List[str]] = None,
) -> Path:
    """
    Create a dated + named output directory.

    Structure:  output/<YYYY-MM-DD>_<HH-MM-SS>_<run_name>/
    Example:    output/2026-04-15_14-32-07_orderbook_run/
    """
    base = Path(output_dir) if output_dir else OUTPUT_DIR

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d_%H-%M-%S")

    if run_name:
        folder_name = f"{date_str}_{run_name}"
    elif spy_types:
        types_label = "_".join(sorted(spy_types))
        folder_name = f"{date_str}_{types_label}"
    else:
        folder_name = date_str

    run_dir = base / folder_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def export_csvs(
    log_path: str,
    run_name: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, str]:
    """
    Parse a log file and export all DataFrames as dated, named CSVs.

    Output goes to:  output/<YYYY-MM-DD>_<HH-MM-SS>_<run_name>/
    Each CSV is named:  <spy_type>.csv  (e.g. orderbook.csv, signals.csv)

    A manifest.json is also written with metadata about the run.

    Parameters
    ----------
    log_path : str
        Path to the Prosperity log file.
    run_name : str, optional
        Human-readable label for this run (e.g. "round1_orderbook").
        If omitted, auto-detected from which spy types are in the log.
    output_dir : str, optional
        Override the base output directory. Defaults to ``output/``
        at the project root.

    Returns
    -------
    dict
        Mapping of dataset name -> output CSV path.
    """
    spy_types = _detect_spy_types(log_path)
    run_dir = _make_run_dir(run_name, output_dir, spy_types)

    data = parse_all(log_path)
    paths = {}
    row_counts = {}

    for name, df in data.items():
        if df is not None and not df.empty:
            csv_path = run_dir / f"{name}.csv"
            df.to_csv(csv_path, index=False)
            paths[name] = str(csv_path)
            row_counts[name] = len(df)
            print(f"  {name}.csv : {len(df):,} rows")
        else:
            print(f"  {name}.csv : skipped (no data)")

    # Write manifest with run metadata
    manifest = {
        "parsed_at": datetime.now().isoformat(),
        "source_log": str(Path(log_path).resolve()),
        "run_name": run_name,
        "spy_types_detected": spy_types,
        "files": {name: {"path": p, "rows": row_counts[name]} for name, p in paths.items()},
    }
    manifest_path = run_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  manifest.json written")

    print(f"\n  Output: {run_dir}")
    return paths


# ======================================================================
# CLI entry point
# ======================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python log_parser.py <log_file> [--name <run_name>] [--output <dir>]")
        print()
        print("Parses a Prosperity simulator log file and exports dated CSVs.")
        print("Handles all spy prefixes: SPY_BOOK|, SPY_OBS|, SPY_TRADE|, SPY_SIG|")
        print()
        print("Options:")
        print("  --name    Label for this run (used in folder name)")
        print("  --output  Override base output directory (default: output/)")
        print()
        print("Examples:")
        print("  python log_parser.py my_log.txt")
        print("    -> output/2026-04-15_14-32-07_book/")
        print()
        print("  python log_parser.py my_log.txt --name round1_deep_book")
        print("    -> output/2026-04-15_14-32-07_round1_deep_book/")
        sys.exit(1)

    log_file = sys.argv[1]
    run_name = None
    out_dir = None

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            run_name = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            out_dir = args[i + 1]
            i += 2
        else:
            i += 1

    print(f"Parsing: {log_file}")
    print("=" * 50)

    paths = export_csvs(log_file, run_name=run_name, output_dir=out_dir)

    print("=" * 50)
    if paths:
        print(f"Done. {len(paths)} file(s) exported.")
    else:
        print("No spy data found in the log file.")

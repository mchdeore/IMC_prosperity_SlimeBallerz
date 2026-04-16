#!/usr/bin/env python3
"""
Deep analysis for tools/sweep_round1.py CSV output.

Beyond a sorted leaderboard, this script:
  - Decodes each config index into the merged ACO/IPR parameter dict (from
    strageties/sweep_submission.py).
  - Reports **stage winners** (best index within each sweep stage, e.g. IPR
    slope indices 29–33).
  - Reports **marginal bests per parameter**: among runs that share a merged
    value for a key (e.g. slope=0.003), the best total PnL — useful when each
    index only perturbs one stage at a time.
  - Optional **Spearman** correlation of numeric parameters vs total PnL.
  - Prints **suggested follow-on experiments** (factorial gaps, local search).

Usage:
  python tools/sweep_round1.py --active IPR --aco-id 3 --ipr-range 0:38 -o results_ipr.csv
  python tools/sweep_deep_analysis.py results_ipr.csv --product INTARIAN_PEPPER_ROOT --focus IPR

  python tools/sweep_round1.py --active ACO --aco-range 0:29 --ipr-id 3 -o results_aco.csv
  python tools/sweep_deep_analysis.py results_aco.csv --product ASH_COATED_OSMIUM --focus ACO
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent

# Must match comments in strageties/sweep_submission.py
IPR_STAGE_RANGES: Tuple[Tuple[str, int, int], ...] = (
    ("A_edge_fill", 0, 7),
    ("B_passive_size", 8, 12),
    ("C_inventory", 13, 21),
    ("D_skew", 22, 25),
    ("E_improve_if_wide", 26, 28),
    ("F_slope", 29, 33),
    ("G_quote_bias", 34, 38),
)

ACO_STAGE_RANGES: Tuple[Tuple[str, int, int], ...] = (
    ("A_edge_fill", 0, 7),
    ("B_passive_size", 8, 12),
    ("C_inventory", 13, 21),
    ("D_ema", 22, 26),
    ("E_improve_if_wide", 27, 29),
)


def _load_sweep_submission():
    """Import sweep_submission with the same datamodel shim as sweep_round1."""
    parent = str(REPO_ROOT / "strageties")
    if parent not in sys.path:
        sys.path.insert(0, parent)

    from prosperity4bt import datamodel as prosperity_datamodel

    sys.modules["datamodel"] = prosperity_datamodel

    import sweep_submission as ss

    return ss


def merged_ipr(ss: Any, ipr_id: int) -> Dict[str, Any]:
    return {**ss.IPR_BASELINE, **ss.IPR_CONFIGS[ipr_id]}


def merged_aco(ss: Any, aco_id: int) -> Dict[str, Any]:
    return {**ss.ACO_BASELINE, **ss.ACO_CONFIGS[aco_id]}


def _stage_for_index(ranges: Tuple[Tuple[str, int, int], ...], idx: int) -> str:
    for name, lo, hi in ranges:
        if lo <= idx <= hi:
            return f"{name}[{lo}:{hi}]"
    return "?"


def aggregate_pnl(df: pd.DataFrame, product: str, id_cols: List[str]) -> pd.DataFrame:
    sub = df[df["product"] == product].copy()
    if sub.empty:
        raise ValueError(f"No rows for product {product!r}")
    g = sub.groupby(id_cols, as_index=False).agg(
        total_pnl=("pnl", "sum"),
        worst_day_pnl=("pnl", "min"),
        n_days=("day", "count"),
    )
    return g


def stage_winners(
    g: pd.DataFrame,
    id_column: str,
    ranges: Tuple[Tuple[str, int, int], ...],
) -> pd.DataFrame:
    rows = []
    for name, lo, hi in ranges:
        part = g[g[id_column].between(lo, hi)]
        if part.empty:
            continue
        best = part.sort_values("total_pnl", ascending=False).iloc[0]
        rows.append(
            {
                "stage": name,
                "index_range": f"{lo}:{hi}",
                "best_config_id": int(best[id_column]),
                "total_pnl": best["total_pnl"],
                "worst_day_pnl": best["worst_day_pnl"],
            }
        )
    return pd.DataFrame(rows)


def marginal_by_param(
    g: pd.DataFrame,
    id_column: str,
    merged_rows: Dict[int, Dict[str, Any]],
) -> pd.DataFrame:
    """For each parameter key, find which value achieved max total_pnl."""
    # Build long table: config_id -> param -> value
    records = []
    for _, row in g.iterrows():
        cfg_id = int(row[id_column])
        m = merged_rows.get(cfg_id, {})
        total = row["total_pnl"]
        for k, v in m.items():
            records.append(
                {
                    id_column: cfg_id,
                    "param": k,
                    "value": v,
                    "total_pnl": total,
                }
            )
    if not records:
        return pd.DataFrame()
    long = pd.DataFrame(records)

    # For each (param, value), take max pnl run (there may be ties)
    bests = long.groupby(["param", "value"], as_index=False).agg(
        best_total_pnl=("total_pnl", "max"),
        n_configs=("total_pnl", "count"),
    )

    # For each param, which value wins
    winners = []
    for param, grp in bests.groupby("param"):
        top = grp.sort_values("best_total_pnl", ascending=False).iloc[0]
        winners.append(
            {
                "param": param,
                "best_value": top["value"],
                "best_total_pnl": top["best_total_pnl"],
                "configs_with_that_value": int(top["n_configs"]),
            }
        )
    out = pd.DataFrame(winners).sort_values("best_total_pnl", ascending=False)
    return out


def spearman_numeric(
    g: pd.DataFrame,
    id_column: str,
    merged_rows: Dict[int, Dict[str, Any]],
) -> pd.DataFrame:
    rows = []
    for _, row in g.iterrows():
        cid = int(row[id_column])
        m = merged_rows[cid]
        r = {"total_pnl": row["total_pnl"], **m}
        rows.append(r)
    wide = pd.DataFrame(rows)
    numeric_cols = [
        c
        for c in wide.columns
        if c != "total_pnl" and wide[c].dtype in ("float64", "int64", "float32", "int32")
    ]
    corrs = []
    pnl = wide["total_pnl"]
    for c in numeric_cols:
        s = wide[c]
        if s.nunique() < 2:
            continue
        # Spearman = Pearson on ranks (avoids pandas/scipy spearman dependency chain).
        r = pnl.rank().corr(s.rank(), method="pearson")
        if not math.isnan(r):
            corrs.append({"param": c, "spearman_vs_total_pnl": r})
    return pd.DataFrame(corrs).sort_values("spearman_vs_total_pnl", key=abs, ascending=False)


def print_follow_ups(
    focus: str,
    best_ipr: Optional[int],
    best_aco: Optional[int],
) -> None:
    print("\n=== Suggested follow-on experiments ===")
    if focus in ("IPR", "BOTH"):
        print(
            "- Stage F (slope) and G (quote_bias) are **not crossed** in IPR_CONFIGS: "
            "each index varies one stage. To fit interactions, run a small custom grid, e.g.\n"
            "  nested loops over slope × quote_bias with fixed baseline (requires a short script\n"
            "  or many BOTH runs — not a single CONFIG_ID)."
        )
    if focus in ("IPR", "BOTH") and best_ipr is not None:
        print(
            f"- **Local search around IPR index {best_ipr}**: try neighboring indices and "
            "`sweep_round1.py --ipr-range` on 29:38 if best is in F/G."
        )
    if focus in ("ACO", "BOTH") and best_aco is not None:
        print(
            f"- **Local search around ACO index {best_aco}**: same for `--aco-range` near the winner."
        )
    print(
        "- **Joint optimization**: `tools/sweep_round1.py --active BOTH --aco-range LO:HI --ipr-range LO:HI` "
        "or `README_EXHAUSTIVE_SWEEP.md` for full Cartesian products (expensive)."
    )
    print(
        "- **Robustness**: use `sweep_analysis_example.py --lodo` and compare to marginal winners "
        "here (configs that win on total may lose on worst-day)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep sweep CSV analysis.")
    parser.add_argument("csv_path", type=Path, help="Output from sweep_round1.py")
    parser.add_argument(
        "--product",
        required=True,
        help="ASH_COATED_OSMIUM or INTARIAN_PEPPER_ROOT",
    )
    parser.add_argument(
        "--focus",
        choices=("ACO", "IPR", "BOTH"),
        default="IPR",
        help="Which config axis to analyze in depth (use IPR for pepper-only sweeps).",
    )
    parser.add_argument("--top", type=int, default=12, help="Leaderboard depth.")
    args = parser.parse_args()

    ss = _load_sweep_submission()

    df = pd.read_csv(args.csv_path)
    if "aco_config_id" not in df.columns or "ipr_config_id" not in df.columns:
        raise SystemExit("CSV must include aco_config_id and ipr_config_id")

    product = args.product
    best_ipr: Optional[int] = None
    best_aco: Optional[int] = None

    if args.focus == "IPR":
        id_col = "ipr_config_id"
        g = aggregate_pnl(df, product, ["ipr_config_id"])
        merged_rows = {i: merged_ipr(ss, i) for i in g["ipr_config_id"].unique()}
        ranges = IPR_STAGE_RANGES

        print(f"=== Leaderboard by total_pnl (top {args.top}) — {product} (IPR index) ===")
        print(
            g.sort_values("total_pnl", ascending=False)
            .head(args.top)
            .to_string(index=False)
        )
        best_ipr = int(g.sort_values("total_pnl", ascending=False).iloc[0]["ipr_config_id"])

        print("\n=== Stage winners (best index within each IPR stage band) ===")
        sw = stage_winners(g, id_col, ranges)
        print(sw.to_string(index=False))

        print("\n=== Marginal 'best value per parameter' (max PnL among configs with that value) ===")
        print(
            "(Many keys can tie at the global max because they match the winner’s baseline; "
            "stage-F `slope` is the main isolated mover on this grid.)"
        )
        marg = marginal_by_param(g, id_col, merged_rows)
        print(marg.to_string(index=False))

        print("\n=== Spearman correlation (numeric params vs total_pnl) ===")
        sp = spearman_numeric(g, id_col, merged_rows)
        if sp.empty:
            print("(no numeric variance)")
        else:
            print(sp.to_string(index=False))

        print("\n=== Best IPR config (full merged dict) ===")
        print(json.dumps(merged_rows[best_ipr], indent=2, sort_keys=True))
        print(f"\n(stage: {_stage_for_index(IPR_STAGE_RANGES, best_ipr)})")

    elif args.focus == "ACO":
        id_col = "aco_config_id"
        g = aggregate_pnl(df, product, ["aco_config_id"])
        merged_rows = {i: merged_aco(ss, i) for i in g["aco_config_id"].unique()}
        ranges = ACO_STAGE_RANGES

        print(f"=== Leaderboard by total_pnl (top {args.top}) — {product} (ACO index) ===")
        print(
            g.sort_values("total_pnl", ascending=False)
            .head(args.top)
            .to_string(index=False)
        )
        best_aco = int(g.sort_values("total_pnl", ascending=False).iloc[0]["aco_config_id"])

        print("\n=== Stage winners (best index within each ACO stage band) ===")
        print(stage_winners(g, id_col, ranges).to_string(index=False))

        print("\n=== Marginal 'best value per parameter' ===")
        print(
            "(Keys shared with the winning row often tie at max PnL; compare **stage winners** "
            "for causal stage-by-stage picks.)"
        )
        print(marginal_by_param(g, id_col, merged_rows).to_string(index=False))

        print("\n=== Spearman correlation ===")
        sp = spearman_numeric(g, id_col, merged_rows)
        print(sp.to_string(index=False) if not sp.empty else "(no numeric variance)")

        print("\n=== Best ACO config (full merged dict) ===")
        print(json.dumps(merged_rows[best_aco], indent=2, sort_keys=True))
        print(f"\n(stage: {_stage_for_index(ACO_STAGE_RANGES, best_aco)})")

    else:
        # BOTH: group by (aco, ipr) pair
        g = aggregate_pnl(df, product, ["aco_config_id", "ipr_config_id"])
        print(f"=== Leaderboard by total_pnl (top {args.top}) — {product} (ACO×IPR) ===")
        print(
            g.sort_values("total_pnl", ascending=False)
            .head(args.top)
            .to_string(index=False)
        )
        top = g.sort_values("total_pnl", ascending=False).iloc[0]
        best_aco = int(top["aco_config_id"])
        best_ipr = int(top["ipr_config_id"])
        print("\n=== Top run merged configs ===")
        print("ACO:", json.dumps(merged_aco(ss, best_aco), indent=2, sort_keys=True))
        print("IPR:", json.dumps(merged_ipr(ss, best_ipr), indent=2, sort_keys=True))

    print_follow_ups(args.focus, best_ipr, best_aco)


if __name__ == "__main__":
    main()

"""Analyze results/exhaustive_full/* sweeps to surface market behavior.

Three CSVs:
  * sweep_aco_isolated.csv  — ACO trades, IPR idle
  * sweep_ipr_isolated.csv  — IPR trades, ACO idle
  * sweep_both_cartesian.csv — cartesian of ACO x IPR configs

Each row has pnl for one (product, day, config-pair).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

RESULTS = Path(__file__).resolve().parents[1] / "results" / "exhaustive_full"


def _load(name: str) -> pd.DataFrame:
    df = pd.read_csv(RESULTS / name)
    df["aco_cfg"] = df["aco_cfg_merged_json"].map(json.loads)
    df["ipr_cfg"] = df["ipr_cfg_merged_json"].map(json.loads)
    return df


def _explode_cfg(df: pd.DataFrame, col: str, keys: list[str]) -> pd.DataFrame:
    for k in keys:
        df[f"{col[:3]}_{k}"] = df[col].map(lambda d, k=k: d.get(k))
    return df


ACO_KEYS = [
    "min_take_edge", "maker_mode", "make_portion", "soft_cap",
    "skew_strength", "ema_alpha", "spread_threshold",
]
IPR_KEYS = [
    "min_take_edge", "maker_mode", "make_portion", "soft_cap",
    "skew_strength", "bid_frac", "spread_threshold", "slope",
    "quote_bias_ticks",
]


def _pretty(df: pd.DataFrame, n: int = 10) -> str:
    return df.head(n).to_string(index=False)


def main() -> None:
    aco_iso = _load("sweep_aco_isolated.csv")
    ipr_iso = _load("sweep_ipr_isolated.csv")
    both    = _load("sweep_both_cartesian.csv")

    aco_iso = aco_iso[aco_iso["product"] == "ASH_COATED_OSMIUM"].copy()
    ipr_iso = ipr_iso[ipr_iso["product"] == "INTARIAN_PEPPER_ROOT"].copy()

    aco_iso = _explode_cfg(aco_iso, "aco_cfg", ACO_KEYS)
    ipr_iso = _explode_cfg(ipr_iso, "ipr_cfg", IPR_KEYS)

    both_aco = both[both["product"] == "ASH_COATED_OSMIUM"].copy()
    both_ipr = both[both["product"] == "INTARIAN_PEPPER_ROOT"].copy()
    both_aco = _explode_cfg(both_aco, "aco_cfg", ACO_KEYS)
    both_ipr = _explode_cfg(both_ipr, "ipr_cfg", IPR_KEYS)

    print("=" * 80)
    print("SHAPE CHECKS")
    print("=" * 80)
    print(f"ACO isolated rows:   {len(aco_iso):>5}  "
          f"(configs={aco_iso['aco_config_id'].nunique()}, days={sorted(aco_iso['day'].unique())})")
    print(f"IPR isolated rows:   {len(ipr_iso):>5}  "
          f"(configs={ipr_iso['ipr_config_id'].nunique()}, days={sorted(ipr_iso['day'].unique())})")
    print(f"BOTH cartesian rows: {len(both):>5}  "
          f"(ACO={both['aco_config_id'].nunique()} x IPR={both['ipr_config_id'].nunique()} "
          f"x days={both['day'].nunique()} x 2 products)")

    # ------------------------------------------------------------------
    # 1. Per-day baseline PnL from isolated sweeps (avg across configs)
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("PER-DAY MEAN PnL (isolated sweeps) — characterizes day difficulty")
    print("=" * 80)
    aco_day = aco_iso.groupby("day")["pnl"].agg(["mean", "std", "min", "max"])
    ipr_day = ipr_iso.groupby("day")["pnl"].agg(["mean", "std", "min", "max"])
    print("\nACO per-day:")
    print(aco_day.round(1).to_string())
    print("\nIPR per-day:")
    print(ipr_day.round(1).to_string())

    # ------------------------------------------------------------------
    # 2. Best isolated configs
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("ACO isolated — config totals across 3 days (top 10 / bottom 5)")
    print("=" * 80)
    aco_by_cfg = (
        aco_iso.groupby(["aco_config_id"] + [f"aco_{k}" for k in ACO_KEYS])["pnl"]
        .agg(["sum", "mean", "std"])
        .reset_index()
        .sort_values("sum", ascending=False)
    )
    print(_pretty(aco_by_cfg, 10))
    print("...")
    print(_pretty(aco_by_cfg.tail(5), 5))

    print("\n" + "=" * 80)
    print("IPR isolated — config totals across 3 days (top 10 / bottom 5)")
    print("=" * 80)
    ipr_by_cfg = (
        ipr_iso.groupby(["ipr_config_id"] + [f"ipr_{k}" for k in IPR_KEYS])["pnl"]
        .agg(["sum", "mean", "std"])
        .reset_index()
        .sort_values("sum", ascending=False)
    )
    print(_pretty(ipr_by_cfg, 10))
    print("...")
    print(_pretty(ipr_by_cfg.tail(5), 5))

    # ------------------------------------------------------------------
    # 3. Marginal effect of each parameter (ACO isolated)
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("ACO — mean PnL by single parameter (marginal over others, isolated sweep)")
    print("=" * 80)
    for key in ["min_take_edge", "maker_mode", "make_portion", "soft_cap",
                "skew_strength", "ema_alpha", "spread_threshold"]:
        col = f"aco_{key}"
        if aco_iso[col].nunique() <= 1:
            continue
        grp = aco_iso.groupby(col)["pnl"].agg(["mean", "std", "count"]).round(1)
        print(f"\n  {key}:")
        print(grp.to_string())

    print("\n" + "=" * 80)
    print("IPR — mean PnL by single parameter (marginal, isolated sweep)")
    print("=" * 80)
    for key in IPR_KEYS:
        col = f"ipr_{key}"
        if ipr_iso[col].nunique() <= 1:
            continue
        grp = ipr_iso.groupby(col)["pnl"].agg(["mean", "std", "count"]).round(1)
        print(f"\n  {key}:")
        print(grp.to_string())

    # ------------------------------------------------------------------
    # 4. Cross-product interaction — does trading IPR affect ACO pnl (and vice versa)?
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("INTERACTION CHECK (cartesian vs isolated) — mean PnL per product / day")
    print("=" * 80)
    cart_aco_by_day = both_aco.groupby("day")["pnl"].agg(["mean", "std"]).round(1)
    cart_ipr_by_day = both_ipr.groupby("day")["pnl"].agg(["mean", "std"]).round(1)
    print("\nACO mean PnL per day, cartesian run:")
    print(cart_aco_by_day.to_string())
    print("\nIPR mean PnL per day, cartesian run:")
    print(cart_ipr_by_day.to_string())

    # Compare same aco config: isolated vs marginalized over IPR
    aco_iso_cfg_mean = (
        aco_iso.groupby("aco_config_id")["pnl"].mean().rename("iso_mean")
    )
    aco_cart_cfg_mean = (
        both_aco.groupby("aco_config_id")["pnl"].mean().rename("cart_mean")
    )
    aco_delta = (
        pd.concat([aco_iso_cfg_mean, aco_cart_cfg_mean], axis=1)
        .assign(delta=lambda d: d["cart_mean"] - d["iso_mean"])
        .sort_values("delta")
    )
    print("\nACO iso vs cartesian (per aco_config_id, mean over days, IPR marginalized):")
    print(f"  mean delta (cart - iso): {aco_delta['delta'].mean():.1f}")
    print(f"  max abs delta:           {aco_delta['delta'].abs().max():.1f}")

    ipr_iso_cfg_mean = (
        ipr_iso.groupby("ipr_config_id")["pnl"].mean().rename("iso_mean")
    )
    ipr_cart_cfg_mean = (
        both_ipr.groupby("ipr_config_id")["pnl"].mean().rename("cart_mean")
    )
    ipr_delta = (
        pd.concat([ipr_iso_cfg_mean, ipr_cart_cfg_mean], axis=1)
        .assign(delta=lambda d: d["cart_mean"] - d["iso_mean"])
        .sort_values("delta")
    )
    print(f"\nIPR iso vs cartesian (per ipr_config_id):")
    print(f"  mean delta (cart - iso): {ipr_delta['delta'].mean():.1f}")
    print(f"  max abs delta:           {ipr_delta['delta'].abs().max():.1f}")

    # ------------------------------------------------------------------
    # 5. Best joint config (cartesian) — maximize combined PnL across days
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("CARTESIAN — top joint configs (sum PnL across 3 days, ACO + IPR)")
    print("=" * 80)
    # Each run is (aco_config_id, ipr_config_id, day) producing 2 rows.
    both["pair"] = list(zip(both["aco_config_id"], both["ipr_config_id"]))
    joint = both.groupby(["aco_config_id", "ipr_config_id"])["pnl"].sum().reset_index()
    joint_day = (
        both.groupby(["aco_config_id", "ipr_config_id", "day"])["pnl"].sum().reset_index()
    )
    joint_day["worst_day"] = joint_day["pnl"]
    worst = joint_day.groupby(["aco_config_id", "ipr_config_id"])["pnl"].min().rename("worst_day")
    joint = joint.merge(worst, on=["aco_config_id", "ipr_config_id"])
    joint = joint.sort_values("pnl", ascending=False)
    joint.columns = ["aco_cfg", "ipr_cfg", "sum_pnl_3d", "worst_day_pnl"]
    print("\nTop 15 joint configs by 3-day total PnL:")
    print(_pretty(joint, 15))
    print("\nTop 15 joint configs by worst-day PnL (robust):")
    print(_pretty(joint.sort_values("worst_day_pnl", ascending=False), 15))

    # Current submission: aco_cfg=2, ipr_cfg=33
    cur = joint[(joint["aco_cfg"] == 2) & (joint["ipr_cfg"] == 33)]
    print(f"\nCurrent optimized submission (aco=2, ipr=33):\n{cur.to_string(index=False)}")
    print(f"Rank by sum_pnl_3d: {(joint['sum_pnl_3d'] > cur['sum_pnl_3d'].iloc[0]).sum() + 1} / {len(joint)}")


if __name__ == "__main__":
    main()

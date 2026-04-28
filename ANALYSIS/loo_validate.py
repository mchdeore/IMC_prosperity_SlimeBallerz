"""Leave-one-day-out CV + plateau stats for round-4 validation sweeps."""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _f(row, key):
    val = row.get(key, "")
    if val == "" or val is None:
        return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


def _load(path):
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def per_day_table(rows):
    headers = ["variant", "day_1", "day_2", "day_3", "total", "min_day", "avg_day", "vev_total"]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        cells = [r.get("variant", "")] + [
            f"{_f(r, k):,.0f}" for k in ["day_1", "day_2", "day_3", "total", "min_day", "avg_day", "vev_total"]
        ]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def loo_cv(rows):
    if not rows:
        return "(no validate_top rows)"
    days = ["day_1", "day_2", "day_3"]
    lines = []
    win_counts = {}
    test_scores = {}
    for held in days:
        train_days = [d for d in days if d != held]
        scored = [
            (r["variant"], sum(_f(r, d) for d in train_days), _f(r, held))
            for r in rows
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        winner_name, winner_train, winner_test = scored[0]
        win_counts[winner_name] = win_counts.get(winner_name, 0) + 1
        for name, _, test in scored:
            test_scores.setdefault(name, []).append(test)
        lines.append(
            f"- Hold-out **{held}**: winner = `{winner_name}` "
            f"(train_avg={winner_train / 2:,.0f} / test={winner_test:,.0f}, "
            f"gap={(winner_train / 2) - winner_test:+,.0f})"
        )
    lines.append("")
    lines.append("**Mean held-out PnL per config:**")
    for name in sorted(test_scores, key=lambda n: -statistics.fmean(test_scores[n])):
        scores = test_scores[name]
        lines.append(
            f"- `{name}`: mean={statistics.fmean(scores):,.0f}, "
            f"min={min(scores):,.0f}, max={max(scores):,.0f}, wins={win_counts.get(name, 0)}/3"
        )
    return "\n".join(lines)


def plateau_stats(rows):
    if not rows:
        return "(no perturb_winner rows)"
    totals = [_f(r, "total") for r in rows]
    mins = [_f(r, "min_day") for r in rows]
    centre = max(rows, key=lambda r: _f(r, "total"))
    centre_total = _f(centre, "total")
    drop_pct = (1 - min(totals) / max(totals)) * 100 if max(totals) > 0 else 0
    lines = [
        f"- combos: {len(rows)}",
        f"- total median: {statistics.median(totals):,.0f}",
        f"- total mean:   {statistics.fmean(totals):,.0f}",
        f"- total min:    {min(totals):,.0f}",
        f"- total max:    {max(totals):,.0f}",
        f"- max drop from peak: {drop_pct:.1f}%",
        f"- min_day median: {statistics.median(mins):,.0f}",
        f"- min_day worst:  {min(mins):,.0f}",
        "",
        f"Best: edge={centre.get('HYDROGEL_TAKE_EDGE')}, "
        f"fallback_w={centre.get('HYDROGEL_FALLBACK_ANCHOR_WEIGHT')}, "
        f"hedge_frac={centre.get('VEV_HEDGE_FRAC')} "
        f"-> total={centre_total:,.0f}, min={_f(centre, 'min_day'):,.0f}",
    ]
    return "\n".join(lines)


def main():
    vt = _load(ROOT / "sweep_r4_validate_top.csv")
    pw = _load(ROOT / "sweep_r4_perturb_winner.csv")
    print("## Per-day PnL\n")
    print(per_day_table(vt))
    print()
    print("## Leave-one-day-out CV\n")
    print(loo_cv(vt))
    print()
    print("## Plateau test\n")
    print(plateau_stats(pw))


if __name__ == "__main__":
    main()

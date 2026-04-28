# Round 4 Quick Validation (Overfit + Performance)

## What changed recently
- Current applied `STRATEGIES/round4.py` has strong **options** knobs (skip `4500`, IV-point smoothing, low z, etc.), but **MM (Hydrogel/Velvet)** is still on old anchors/edges and no fallback.

## Validate set (5 configs)
- `current_applied` (as-is)
- `safe_mm_no_vev`
- `safe_mm_skip_4500`
- `total_hydro_skip_4500` (Hydrogel fallback weight 0.8)
- `safe_mm_skip_4500_hedge` (same as `safe_mm_skip_4500` plus hedge knobs)

Source: `sweep_r4_validate_top.csv`.

## Per-day PnL (R4 day1/day2/day3)

| variant | day_1 | day_2 | day_3 | total | min_day | avg_day | vev_total |
|---|---|---|---|---|---|---|---|
| current_applied | 54,837 | 43,165 | 66,066 | 164,068 | 43,165 | 54,689 | 26,343 |
| safe_mm_no_vev | 71,352 | 60,033 | 86,874 | 218,259 | 60,033 | 72,753 | 0 |
| safe_mm_skip_4500 | 81,061 | 73,516 | 90,025 | 244,602 | 73,516 | 81,534 | 26,343 |
| total_hydro_skip_4500 | 81,820 | 70,956 | 97,291 | 250,067 | 70,956 | 83,356 | 26,343 |
| safe_mm_skip_4500_hedge | 81,061 | 73,516 | 90,025 | 244,602 | 73,516 | 81,534 | 26,343 |

Key points:
- Big uplift comes from **MM changes**, not extra option tweaking: `current_applied` → `safe_mm_skip_4500` is **+80k total** and **+30k min-day**.
- Hedge knobs (as tested) do **not** change totals vs `safe_mm_skip_4500` (identical per-day here).
- `total_hydro_skip_4500` has best total/avg, but worse day2 than `safe_mm_skip_4500`.

## Leave-one-day-out check (quick overfit test)

- Hold-out **day_1**: winner = `total_hydro_skip_4500` (small gap)
- Hold-out **day_2**: winner = `total_hydro_skip_4500` (gap is large; suggests day2 is the hardest day)
- Hold-out **day_3**: winner = `safe_mm_skip_4500`

Interpretation:
- Both top configs look **general** across days; there is no “wins only one day” behavior.
- The decision is mostly: **higher day3 vs safer day2**.

## Quick plateau sanity (9-run perturb)

Source: `sweep_r4_perturb_winner.csv` (varied Hydrogel take edge 6/7/8 and hedge frac 0.15/0.25/0.35).

- total min/max: **233,644 → 244,925** (max drop 4.6% from peak)
- min_day worst: **67,575**
- Best found: `HYDROGEL_TAKE_EDGE=8`, `HYDROGEL_FALLBACK_ANCHOR_WEIGHT=0.5`, `VEV_HEDGE_FRAC=0.15` → **244,925 total**, **73,257 min-day**

This is a **plateau**, not a single-spike winner.

## Hypotheses (why it behaves this way)

- **Hydrogel fallback**: `anchor=9998` with drift-based fallback prevents “anchor wrong day” blowups while still letting you mean-revert to anchor. This mainly lifts **min-day**.
- **Velvet anchor 5248 + edge 12 + larger caps**: reduces churn and adverse selection; the old `HARDCAP=80` and small take edge likely left money on the table.
- **VEV skip only 4500**: allows `VEV_4000` to trade (large, consistent contributor in prior option sweeps) without opening the worst deep strike.
- **IV-point smoothing**: stabilizes fair values per strike so the strategy can quote/take without noisy IV fits.

## Recommendation (fast)

- If you want **safer min-day**: pick `safe_mm_skip_4500` style and then decide Hydrogel fallback weight `0.5` vs `0.8` with a 2-run check focusing on day2.\n+- If you want **max total**: pick `total_hydro_skip_4500`.\n+
Next smallest “build” step: apply the chosen MM constants into `STRATEGIES/round4.py` (Hydrogel fallback + Velvet caps/edge) while keeping current options knobs.


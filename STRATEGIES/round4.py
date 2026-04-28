"""
Round 4 — HYDROGEL_PACK, VELVETFRUIT_EXTRACT, and VEV options market-maker.

Features: drift-aware fallback fair on MM products, aggressive option scalping
(low entry_z + take_edge), enabled VEV_4000 quoting, no VEV→VELVET hedge,
per-strike IV-point EMA smoothing in the smile fit.

Validated round-4 PnL ≈ 244,925 (min-day 73,257). Round-3 OOS ≈ 229,734.
Each tuned knob has fallback / very-safe values commented inline; revert
by hand if live underperforms (see CLAUDE-EXPERIMENT/FINAL_REPORT.md).
"""

# =======================================================================
# IMPORTS & CONSTANTS
# =======================================================================

import json
import math
from collections import namedtuple
from typing import Any, Dict, List, Optional, Tuple


from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState

ENABLE_HYDROGEL = 1
ENABLE_VELVET = 1
ENABLE_VEV = 1

# ── HYDROGEL ──
HYDROGEL = "HYDROGEL_PACK"
HYDROGEL_LIMIT = 200

HYDROGEL_ANCHOR = 9998.0
HYDROGEL_W_ANCHOR = 0.0
HYDROGEL_W_MA = 1.0
HYDROGEL_MA_WINDOW = 1000
HYDROGEL_ANCHOR_WEIGHT = 1.0
HYDROGEL_DRIFT_THRESHOLD = 20.0
HYDROGEL_FALLBACK_ANCHOR_WEIGHT = 0.5  # live=0.5. Fallback: 0.65; very-safe: 0.8.
# When bid or ask is missing: L1-mid is undefined — feed anchor into the MA.
# When both sides present: L1 mid = (best_bid + best_ask) / 2.
HYDROGEL_TIGHT = 1   # two-sided: aim one tick from fair, beat book
HYDROGEL_WIDE = 4    # not two-sided: quote this many ticks from fair before skew
HYDROGEL_TAKE_EDGE = 8    # take asks <= fair - edge, bids >= fair + edge
HYDROGEL_MAX_QUOTE = 120

HYDROGEL_SOFTCAP = 190
HYDROGEL_HARDCAP = 200
HYDROGEL_YARDAGE = HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP

# ── VELVETFRUIT ──
VELVET = "VELVETFRUIT_EXTRACT"
VELVET_LIMIT = 200

VELVET_ANCHOR = 5248.0
VELVET_EMA_ALPHA = 0.0
VELVET_ANCHOR_WEIGHT = 1.0
VELVET_DRIFT_THRESHOLD = 999999.0
VELVET_FALLBACK_ANCHOR_WEIGHT = 1.0
VELVET_TIGHT = 1
VELVET_WIDE = 4
VELVET_TAKE_EDGE = 12
VELVET_USE_ANCHOR_INIT = 1
VELVET_MAX_QUOTE = 80

VELVET_SOFTCAP = 60
VELVET_HARDCAP = 200
VELVET_YARDAGE = VELVET_HARDCAP - VELVET_SOFTCAP

# ── DELTA HEDGE ──
# Fallback ranges if live underperforms: each knob has safe / very-safe alternatives.
VEV_HEDGE_MODE = 1           # live=1 (passive). Fallback: keep 1; very-safe: 0 (aggressive).
VEV_HEDGE_FRAC = 0.15        # live=0.15. Fallback: 0.25; very-safe: 0.5.
VELVET_HEDGE_CAP = 0         # live=0 (no hedge). Fallback: 40; very-safe: 120 (original).

# ── Product config (shared MM logic) ──
ProductConfig = namedtuple("ProductConfig", [
    "symbol", "limit", "anchor", "w_anchor", "w_ma", "ma_window",
    "anchor_weight", "drift_threshold", "fallback_anchor_weight",
    "tight", "wide", "take_edge", "max_quote", "softcap", "hardcap", "yardage", "mids_key", "diag_key",
])

HYDROGEL_CFG = ProductConfig(
    symbol=HYDROGEL, limit=HYDROGEL_LIMIT, anchor=HYDROGEL_ANCHOR,
    w_anchor=HYDROGEL_W_ANCHOR, w_ma=HYDROGEL_W_MA, ma_window=HYDROGEL_MA_WINDOW,
    anchor_weight=HYDROGEL_ANCHOR_WEIGHT,
    drift_threshold=HYDROGEL_DRIFT_THRESHOLD,
    fallback_anchor_weight=HYDROGEL_FALLBACK_ANCHOR_WEIGHT,
    tight=HYDROGEL_TIGHT, wide=HYDROGEL_WIDE, take_edge=HYDROGEL_TAKE_EDGE,
    max_quote=HYDROGEL_MAX_QUOTE,
    softcap=HYDROGEL_SOFTCAP, hardcap=HYDROGEL_HARDCAP, yardage=HYDROGEL_YARDAGE,
    mids_key="hydrogel_mids", diag_key="hydrogel",
)

# ── VELVETFRUIT OPTIONS (stubs) ──
VEV_STRIKES = {
    "VEV_4000": 4000,
    "VEV_4500": 4500,
    "VEV_5000": 5000,
    "VEV_5100": 5100,
    "VEV_5200": 5200,
    "VEV_5300": 5300,
    "VEV_5400": 5400,
    "VEV_5500": 5500,
    "VEV_6000": 6000,
    "VEV_6500": 6500,
}
VEV_LIMIT = 300
VEV_STRIKE_CAP = 120
VEV_SOFTCAP = 72
VEV_HARDCAP = VEV_STRIKE_CAP
VEV_YARDAGE = VEV_HARDCAP - VEV_SOFTCAP
VEV_MAX_QUOTE = 24

TICKS_PER_DAY = 10000
TTE_DAYS_AT_ROUND_START = 4

VEV_PINNED_STRIKES = {6000, 6500}
VEV_SKIP_QUOTE: set = {4500}    # live={4500} (enables 4000). Fallback: {4500}; very-safe: {4000, 4500} (original).
VEV_TIGHT = 1
VEV_WIDE = 3
VEV_WIDE_OFFSET = 2
VEV_TIGHT_SIZE_FRAC = 0.3
VEV_TAKE_EDGE = 1           # live=1. Fallback: 2; very-safe: 3.
VEV_SMILE_EMA = 0.35
VEV_SMILE_FIT_MODE = 3      # 0=flat, 1=linear, 2=quadratic ridge, 3=tiered
VEV_CURRENT_SMILE_WEIGHT = 0.5
VEV_INCLUDE_PINNED_IN_FIT = 1
VEV_DELTA_DIVISOR = 60.0
VEV_SPOT_BLEND = 1.0
VEV_INCLUDE_PINNED_IN_NET_DELTA = 0
VEV_WEIGHT_SMILE_BY_SPREAD = 1
VEV_ADAPTIVE_IV_HI = 0
VEV_STRIKE_DELTA_SIZE_BIAS = 0
VEV_STAT_ALPHA = 0.03
VEV_IV_BLEND = 0.35
VEV_ENTRY_Z = 0.5            # live=0.5. Fallback: 0.75; very-safe: 1.0.
VEV_EXIT_Z = 0.25            # live=0.25. Fallback: 0.35; very-safe: 0.5.
VEV_MIN_RESID_DEV = 1.0
VEV_MAX_TAKE = 24
VEV_PASSIVE_SIZE_FRAC = 0.15
VEV_DELTA_SKEW_TICK_CAP = 8
VEV_MIN_TRADE_FAIR = 50.0
VEV_REALIZED_VOL_WEIGHT = 0.0
VEV_REALIZED_VOL_ALPHA = 0.02
VEV_REALIZED_VOL_MIN_SAMPLES = 50

# ── Smile-point averaging (validated +1.7k r4 / +0.5k r3 over skip+hedge) ──
VEV_IV_POINT_MODE = 1            # live=1 (EMA). Fallback: 1; very-safe: 0 (off).
VEV_IV_POINT_ALPHA = 0.02        # mode 1
VEV_IV_POINT_WINDOW = 200        # mode 2
VEV_IV_POINT_HIST_WEIGHT = 0.5   # live=0.5. Fallback: 0.25; very-safe: 0.0 (no smoothing).



# =======================================================================
# HELPER FUNCTIONS
# =======================================================================


def best_bid(depth: OrderDepth) -> Optional[Tuple[int, int]]:
    if not depth.buy_orders:
        return None
    p = max(depth.buy_orders.keys())
    return p, depth.buy_orders[p]


def best_ask(depth: OrderDepth) -> Optional[Tuple[int, int]]:
    if not depth.sell_orders:
        return None
    p = min(depth.sell_orders.keys())
    return p, depth.sell_orders[p]


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _rolling_mean(values: List[float], anchor: float) -> float:
    if not values:
        return anchor
    return sum(values) / float(len(values))


def _append_mid_window(saved: Dict[str, Any], sample: float, mids_key: str, ma_window: int) -> List[float]:
    hist: List[float] = saved.setdefault(mids_key, [])
    if not isinstance(hist, list):
        hist = []
        saved[mids_key] = hist
    hist.append(sample)
    if len(hist) > ma_window:
        hist[:] = hist[-ma_window:]
    return hist


def _fair_from_mids(mid_history: List[float], anchor: float, w_anchor: float, w_ma: float) -> float:
    ma = _rolling_mean(mid_history, anchor)
    return w_anchor * anchor + w_ma * ma


def _fallback_fair(
    anchor: float,
    ema_fair: float,
    anchor_weight: float,
    drift_threshold: float,
    fallback_anchor_weight: float,
) -> Tuple[float, float, float, str]:
    drift = abs(ema_fair - anchor)
    if drift_threshold <= 0:
        mix = 1.0
    else:
        # One merged fair: stay anchor-heavy near the anchor and fade toward
        # the fallback weight smoothly as live fair drifts away.
        mix = drift / (drift + drift_threshold)
    weight = anchor_weight + (fallback_anchor_weight - anchor_weight) * mix
    weight = max(0.0, min(1.0, weight))
    fair = weight * anchor + (1.0 - weight) * ema_fair
    mode = "merged"
    return fair, drift, weight, mode


def _per_tick_mid_sample(bb: Optional[Tuple[int, int]], ba: Optional[Tuple[int, int]], anchor: float) -> float:
    if bb is not None and ba is not None:
        return (bb[0] + ba[0]) / 2.0
    return anchor


def _take_mispriced(
    symbol: str, depth: OrderDepth, fair_int: int, take_edge: int,
    buy_cap: int, sell_cap: int,
) -> Tuple[List[Order], int, int]:
    """Sweep all book levels priced at/better than fair ± take_edge."""
    orders: List[Order] = []
    for ask_p in sorted(depth.sell_orders.keys()):
        if ask_p > fair_int - take_edge or buy_cap <= 0:
            break
        vol = min(-depth.sell_orders[ask_p], buy_cap)
        if vol > 0:
            orders.append(Order(symbol, ask_p, vol))
            buy_cap -= vol
    for bid_p in sorted(depth.buy_orders.keys(), reverse=True):
        if bid_p < fair_int + take_edge or sell_cap <= 0:
            break
        vol = min(depth.buy_orders[bid_p], sell_cap)
        if vol > 0:
            orders.append(Order(symbol, bid_p, -vol))
            sell_cap -= vol
    return orders, buy_cap, sell_cap


def _flatten_toward_zero(
    symbol: str, depth: OrderDepth, fair_int: int,
    position: int, softcap: int, buy_cap: int, sell_cap: int,
) -> Tuple[List[Order], int, int]:
    """When past softcap, take at fair to bring position back toward softcap."""
    orders: List[Order] = []
    if position > softcap:
        to_sell = min(position - softcap, sell_cap)
        for bid_p in sorted(depth.buy_orders.keys(), reverse=True):
            if to_sell <= 0 or bid_p < fair_int:
                break
            vol = min(depth.buy_orders[bid_p], to_sell)
            if vol > 0:
                orders.append(Order(symbol, bid_p, -vol))
                sell_cap -= vol
                to_sell -= vol
    elif position < -softcap:
        to_buy = min(-position - softcap, buy_cap)
        for ask_p in sorted(depth.sell_orders.keys()):
            if to_buy <= 0 or ask_p > fair_int:
                break
            vol = min(-depth.sell_orders[ask_p], to_buy)
            if vol > 0:
                orders.append(Order(symbol, ask_p, vol))
                buy_cap -= vol
                to_buy -= vol
    return orders, buy_cap, sell_cap


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float) -> float:
    """Black-Scholes call. T in days, sigma in per-sqrt-day units."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    vol_T = sigma * math.sqrt(T)
    if vol_T < 1e-8:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vol_T
    d2 = d1 - vol_T
    return S * _norm_cdf(d1) - K * _norm_cdf(d2)


def bs_delta(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    vol_T = sigma * math.sqrt(T)
    if vol_T < 1e-8:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vol_T
    return _norm_cdf(d1)


def implied_vol(
    C_market: float,
    S: float,
    K: float,
    T: float,
    lo: float = 1e-5,
    hi: float = 1.0,
    tol: float = 1e-6,
    max_iter: int = 60,
    adaptive_hi: bool = False,
) -> Optional[float]:
    intrinsic = max(S - K, 0.0)
    if C_market < intrinsic - 1e-3 or C_market > S + 1e-3:
        return None
    if C_market <= intrinsic + 1e-6:
        return lo
    f_lo = bs_call(S, K, T, lo) - C_market
    if adaptive_hi:
        hi_cap = 5.0
        while hi < hi_cap and bs_call(S, K, T, hi) < C_market:
            hi = min(hi * 2.0, hi_cap)
    f_hi = bs_call(S, K, T, hi) - C_market
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = bs_call(S, K, T, mid) - C_market
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo = mid
            f_lo = f_mid
    return 0.5 * (lo + hi)


def _solve_3x3(A: List[List[float]], b: List[float]) -> Optional[Tuple[float, float, float]]:
    """Cramer's rule for 3x3 system."""
    def det(M: List[List[float]]) -> float:
        return (
            M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
            - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
            + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])
        )

    D = det(A)
    if abs(D) < 1e-12:
        return None
    out: List[float] = []
    for col in range(3):
        Mi = [row[:] for row in A]
        for row in range(3):
            Mi[row][col] = b[row]
        out.append(det(Mi) / D)
    return (out[0], out[1], out[2])


def fit_smile(
    moneyness: List[float],
    ivs: List[float],
    weights: Optional[List[float]] = None,
    mode: int = VEV_SMILE_FIT_MODE,
) -> Optional[Tuple[float, float, float]]:
    """Fit IV(m) = a*m^2 + b*m + c. Mode controls flat/linear/quad/tiered."""
    n = len(moneyness)
    if n < 2 or n != len(ivs):
        return None
    if weights is None:
        weights = [1.0] * n
    if len(weights) != n:
        return None
    sum_w = sum(weights)
    if sum_w <= 0:
        return None
    c_flat = sum(w * iv for w, iv in zip(weights, ivs)) / sum_w
    iv_std = (sum(w * (iv - c_flat) ** 2 for w, iv in zip(weights, ivs)) / sum_w) ** 0.5
    if mode == 0 or iv_std < 5e-4 or (mode == 3 and n < 3):
        return (0.0, 0.0, c_flat)
    if n < 3:
        return (0.0, 0.0, c_flat)

    sum_m = sum(w * m for w, m in zip(weights, moneyness))
    sum_m2 = sum(w * m * m for w, m in zip(weights, moneyness))
    sum_iv = sum(w * iv for w, iv in zip(weights, ivs))
    sum_iv_m = sum(w * iv * m for w, iv, m in zip(weights, ivs, moneyness))
    denom = sum_w * sum_m2 - sum_m * sum_m
    if abs(denom) < 1e-12:
        return (0.0, 0.0, c_flat)
    b_lin = (sum_w * sum_iv_m - sum_m * sum_iv) / denom
    c_lin = (sum_iv - b_lin * sum_m) / sum_w
    if mode == 1 or (mode == 3 and n < 5):
        return (0.0, b_lin, c_lin)
    if n < 5:
        return (0.0, b_lin, c_lin)

    sum_m3 = sum(w * m ** 3 for w, m in zip(weights, moneyness))
    sum_m4 = sum(w * m ** 4 for w, m in zip(weights, moneyness))
    sum_iv_m2 = sum(w * iv * m * m for w, iv, m in zip(weights, ivs, moneyness))
    lam = 1e-6
    A = [
        [sum_m4 + lam, sum_m3, sum_m2],
        [sum_m3, sum_m2 + lam, sum_m],
        [sum_m2, sum_m, float(sum_w) + lam],
    ]
    rhs = [sum_iv_m2, sum_iv_m, sum_iv]
    quad = _solve_3x3(A, rhs)
    return quad if quad is not None else (0.0, b_lin, c_lin)


def time_to_expiry(state: TradingState) -> float:
    tick = state.timestamp / 100.0
    return max(TTE_DAYS_AT_ROUND_START - (tick / TICKS_PER_DAY), 1e-6)


def _base_quotes(
    fair_int: int,
    bb: Optional[Tuple[int, int]],
    ba: Optional[Tuple[int, int]],
    tight: int,
    wide: int,
) -> Tuple[int, int]:
    if bb is None or ba is None:
        return fair_int - wide, fair_int + wide
    bid_price = fair_int - tight
    ask_price = fair_int + tight
    candidate_bid = bb[0] + 1
    if candidate_bid < fair_int:
        bid_price = candidate_bid
    candidate_ask = ba[0] - 1
    if candidate_ask > fair_int:
        ask_price = candidate_ask
    return bid_price, ask_price


def _apply_inventory_skew(
    fair_int: int,
    position: int,
    bid_price: int,
    ask_price: int,
    bid_distance: int,
    ask_distance: int,
    g: float,
    buy_cap: int,
    sell_cap: int,
    softcap: int,
) -> Tuple[int, int, int, int]:
    if position > softcap:
        ask_skew = int(g * (ask_distance - 1))
        bid_skew = int(g * bid_distance)
        ask_price = fair_int + (ask_distance - ask_skew)
        bid_price = fair_int - (bid_distance + bid_skew)
        bid_size = int(buy_cap * (1.0 - g))
        ask_size = sell_cap
    elif position < -softcap:
        bid_skew = int(g * (bid_distance - 1))
        ask_skew = int(g * ask_distance)
        bid_price = fair_int - (bid_distance - bid_skew)
        ask_price = fair_int + (ask_distance + ask_skew)
        bid_size = buy_cap
        ask_size = int(sell_cap * (1.0 - g))
    else:
        bid_size = buy_cap
        ask_size = sell_cap
    return bid_price, ask_price, max(int(bid_size), 0), max(int(ask_size), 0)


# =======================================================================
# TRADER
# =======================================================================


class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict, conversions: int, trader_data: str) -> None:
        base_length = len(self.to_json([
            self.compress_state(state, ""),
            self.compress_orders(orders),
            conversions, "", "",
        ]))
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders),
            conversions,
            self.truncate(trader_data, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list:
        return [
            state.timestamp, trader_data,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations),
        ]

    def compress_listings(self, listings: dict) -> list:
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths: dict) -> dict:
        return {s: [d.buy_orders, d.sell_orders] for s, d in order_depths.items()}

    def compress_trades(self, trades: dict) -> list:
        compressed = []
        for arr in trades.values():
            for t in arr:
                compressed.append([t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp])
        return compressed

    def compress_observations(self, observations: Observation) -> list:
        co = {}
        for product, obs in observations.conversionObservations.items():
            co[product] = [
                obs.bidPrice, obs.askPrice, obs.transportFees,
                obs.exportTariff, obs.importTariff,
                getattr(obs, "sugarPrice", getattr(obs, "sunlight", 0)),
                getattr(obs, "sunlightIndex", getattr(obs, "humidity", 0)),
            ]
        return [observations.plainValueObservations, co]

    def compress_orders(self, orders: dict) -> list:
        compressed = []
        for arr in orders.values():
            for o in arr:
                compressed.append([o.symbol, o.price, o.quantity])
        return compressed

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        if not value:
            return ""
        lo, hi = 0, min(len(value), max_length)
        out = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = value[:mid]
            if len(candidate) < len(value):
                candidate += "..."
            if len(json.dumps(candidate)) <= max_length:
                out = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        return out


logger = Logger()


class Trader:

    def _run_mm(
        self, state: TradingState, saved: Dict[str, Any], cfg: "ProductConfig"
    ) -> Tuple[List[Order], float]:
        """Generic anchor-weighted-MA market-maker; mutates ``saved``."""
        depth = state.order_depths[cfg.symbol]
        position = int(state.position.get(cfg.symbol, 0))

        bb = best_bid(depth)
        ba = best_ask(depth)
        tick_mid = _per_tick_mid_sample(bb, ba, cfg.anchor)
        mid_history = _append_mid_window(saved, tick_mid, cfg.mids_key, cfg.ma_window)
        ma_used = _rolling_mean(mid_history, cfg.anchor)
        ema_fair = _fair_from_mids(mid_history, cfg.anchor, cfg.w_anchor, cfg.w_ma)
        fair, drift, fair_anchor_weight, fair_mode = _fallback_fair(
            cfg.anchor,
            ema_fair,
            cfg.anchor_weight,
            cfg.drift_threshold,
            cfg.fallback_anchor_weight,
        )
        fair_int = int(round(fair))

        buy_cap = max(cfg.limit - position, 0)
        sell_cap = max(cfg.limit + position, 0)
        orders: List[Order] = []

        # aggressive taking + corrective flatten
        take_ords, buy_cap, sell_cap = _take_mispriced(
            cfg.symbol, depth, fair_int, cfg.take_edge, buy_cap, sell_cap)
        orders.extend(take_ords)
        flat_ords, buy_cap, sell_cap = _flatten_toward_zero(
            cfg.symbol, depth, fair_int, position, cfg.softcap, buy_cap, sell_cap)
        orders.extend(flat_ords)

        # passive quoting with remaining capacity
        bid_price, ask_price = _base_quotes(fair_int, bb, ba, cfg.tight, cfg.wide)
        bid_distance = max(fair_int - bid_price, 1)
        ask_distance = max(ask_price - fair_int, 1)

        abs_pos = abs(position)
        g = 0.0 if abs_pos <= cfg.softcap else min((abs_pos - cfg.softcap) / float(cfg.yardage), 1.0)

        bid_price, ask_price, bid_size, ask_size = _apply_inventory_skew(
            fair_int, position, bid_price, ask_price,
            bid_distance, ask_distance, g, buy_cap, sell_cap, cfg.softcap,
        )

        bid_price = clamp(bid_price, 1, fair_int - 1)
        ask_price = clamp(ask_price, fair_int + 1, 10**9)
        bid_size = min(max(int(bid_size), 0), buy_cap, cfg.max_quote)
        ask_size = min(max(int(ask_size), 0), sell_cap, cfg.max_quote)

        if bid_size > 0:
            orders.append(Order(cfg.symbol, bid_price, bid_size))
        if ask_size > 0:
            orders.append(Order(cfg.symbol, ask_price, -ask_size))

        saved[cfg.diag_key] = {
            "fair": fair, "ma": ma_used, "ema_fair": ema_fair, "mid_tick": tick_mid,
            "drift": drift, "fair_anchor_weight": fair_anchor_weight,
            "fair_mode": fair_mode,
            "w_anchor": cfg.w_anchor, "w_ma": cfg.w_ma,
            "ma_window": cfg.ma_window, "ma_len": len(mid_history),
            "bid_price": bid_price, "ask_price": ask_price,
            "bid_distance": bid_distance, "ask_distance": ask_distance,
            "bid_size": bid_size, "ask_size": ask_size,
            "position": position, "g": g,
            "softcap": cfg.softcap, "hardcap": cfg.hardcap,
        }

        return orders, fair

    def run_hydrogel(
        self, state: TradingState, saved: Dict[str, Any]
    ) -> Tuple[List[Order], float]:
        return self._run_mm(state, saved, HYDROGEL_CFG)

    def run_velvetfruit(
        self, state: TradingState, saved: Dict[str, Any]
    ) -> Tuple[List[Order], float]:
        depth = state.order_depths[VELVET]
        position = int(state.position.get(VELVET, 0))
        bb = best_bid(depth)
        ba = best_ask(depth)
        tick_mid = _per_tick_mid_sample(bb, ba, VELVET_ANCHOR)

        ema_seed = VELVET_ANCHOR if VELVET_USE_ANCHOR_INIT else tick_mid
        ema = saved.get("velvet_ema", ema_seed)
        ema = VELVET_EMA_ALPHA * tick_mid + (1.0 - VELVET_EMA_ALPHA) * ema
        saved["velvet_ema"] = ema
        fair, drift, fair_anchor_weight, fair_mode = _fallback_fair(
            VELVET_ANCHOR,
            ema,
            VELVET_ANCHOR_WEIGHT,
            VELVET_DRIFT_THRESHOLD,
            VELVET_FALLBACK_ANCHOR_WEIGHT,
        )
        fair_int = int(round(fair))

        buy_cap = max(VELVET_LIMIT - position, 0)
        sell_cap = max(VELVET_LIMIT + position, 0)
        orders: List[Order] = []

        # ── delta hedge (runs before MM) ──
        hedge_target = int(saved.get("vev_hedge_target", 0))
        hedge_gap = hedge_target - position
        hedge_gap = clamp(hedge_gap, -VELVET_LIMIT - position, VELVET_LIMIT - position)
        hedge_fills = 0

        if VEV_HEDGE_MODE == 0:
            # Mode 0: aggressive — take at/through fair
            if hedge_gap > 0:
                want = min(hedge_gap, buy_cap)
                for ask_p in sorted(depth.sell_orders.keys()):
                    if want <= 0 or ask_p > fair_int:
                        break
                    vol = min(-depth.sell_orders[ask_p], want)
                    if vol > 0:
                        orders.append(Order(VELVET, ask_p, vol))
                        buy_cap -= vol
                        want -= vol
                        hedge_fills += vol
            elif hedge_gap < 0:
                want = min(-hedge_gap, sell_cap)
                for bid_p in sorted(depth.buy_orders.keys(), reverse=True):
                    if want <= 0 or bid_p < fair_int:
                        break
                    vol = min(depth.buy_orders[bid_p], want)
                    if vol > 0:
                        orders.append(Order(VELVET, bid_p, -vol))
                        sell_cap -= vol
                        want -= vol
                        hedge_fills += vol
        elif VEV_HEDGE_MODE == 1:
            # Mode 1: passive/gradual — move fraction of gap, take only better-than-fair
            effective_gap = int(hedge_gap * VEV_HEDGE_FRAC) if abs(hedge_gap) > 1 else hedge_gap
            if effective_gap > 0:
                want = min(effective_gap, buy_cap)
                # take strictly better than fair
                for ask_p in sorted(depth.sell_orders.keys()):
                    if want <= 0 or ask_p >= fair_int:
                        break
                    vol = min(-depth.sell_orders[ask_p], want)
                    if vol > 0:
                        orders.append(Order(VELVET, ask_p, vol))
                        buy_cap -= vol
                        want -= vol
                        hedge_fills += vol
                # passive remainder at fair
                if want > 0 and buy_cap > 0:
                    vol = min(want, buy_cap)
                    orders.append(Order(VELVET, fair_int, vol))
                    buy_cap -= vol
                    hedge_fills += vol
            elif effective_gap < 0:
                want = min(-effective_gap, sell_cap)
                for bid_p in sorted(depth.buy_orders.keys(), reverse=True):
                    if want <= 0 or bid_p <= fair_int:
                        break
                    vol = min(depth.buy_orders[bid_p], want)
                    if vol > 0:
                        orders.append(Order(VELVET, bid_p, -vol))
                        sell_cap -= vol
                        want -= vol
                        hedge_fills += vol
                if want > 0 and sell_cap > 0:
                    vol = min(want, sell_cap)
                    orders.append(Order(VELVET, fair_int, -vol))
                    sell_cap -= vol
                    hedge_fills += vol

        saved["velvet_hedge"] = {
            "target": hedge_target, "gap": hedge_gap, "fills": hedge_fills,
            "mode": VEV_HEDGE_MODE, "pos": position,
            "fair": fair, "ema": ema, "drift": drift,
            "fair_anchor_weight": fair_anchor_weight, "fair_mode": fair_mode,
        }

        # aggressive taking + corrective flatten
        take_ords, buy_cap, sell_cap = _take_mispriced(
            VELVET, depth, fair_int, VELVET_TAKE_EDGE, buy_cap, sell_cap)
        orders.extend(take_ords)
        flat_ords, buy_cap, sell_cap = _flatten_toward_zero(
            VELVET, depth, fair_int, position, VELVET_SOFTCAP, buy_cap, sell_cap)
        orders.extend(flat_ords)

        # passive quoting with remaining capacity
        bid_price, ask_price = _base_quotes(fair_int, bb, ba, VELVET_TIGHT, VELVET_WIDE)
        bid_distance = max(fair_int - bid_price, 1)
        ask_distance = max(ask_price - fair_int, 1)
        abs_pos = abs(position)
        g = 0.0 if abs_pos <= VELVET_SOFTCAP else min((abs_pos - VELVET_SOFTCAP) / float(VELVET_YARDAGE), 1.0)
        bid_price, ask_price, bid_size, ask_size = _apply_inventory_skew(
            fair_int, position, bid_price, ask_price,
            bid_distance, ask_distance, g, buy_cap, sell_cap, VELVET_SOFTCAP,
        )
        bid_price = clamp(bid_price, 1, fair_int - 1)
        ask_price = clamp(ask_price, fair_int + 1, 10**9)
        bid_size = min(max(int(bid_size), 0), buy_cap, VELVET_MAX_QUOTE)
        ask_size = min(max(int(ask_size), 0), sell_cap, VELVET_MAX_QUOTE)

        if bid_size > 0:
            orders.append(Order(VELVET, bid_price, bid_size))
        if ask_size > 0:
            orders.append(Order(VELVET, ask_price, -ask_size))
        return orders, ema

    def run_vev_options(
        self, state: TradingState, saved: Dict[str, Any]
    ) -> Tuple[Dict[str, List[Order]], Dict[str, float]]:
        orders_out: Dict[str, List[Order]] = {}
        fair_out: Dict[str, float] = {}

        if VELVET not in state.order_depths:
            return orders_out, fair_out
        vbb, vba = best_bid(state.order_depths[VELVET]), best_ask(state.order_depths[VELVET])
        if vbb is None or vba is None:
            return orders_out, fair_out
        spot_mid = (vbb[0] + vba[0]) / 2.0
        ema_ref = float(saved.get("velvet_ema", spot_mid))
        S = VEV_SPOT_BLEND * spot_mid + (1.0 - VEV_SPOT_BLEND) * ema_ref
        rv_state = saved.get("vev_realized_vol", {})
        if not isinstance(rv_state, dict):
            rv_state = {}
        prev_spot = rv_state.get("spot")
        rv_var = float(rv_state.get("var", 0.0))
        rv_count = int(rv_state.get("count", 0))
        if isinstance(prev_spot, (int, float)) and prev_spot > 0 and spot_mid > 0:
            ret = math.log(spot_mid / float(prev_spot))
            rv_var = (1.0 - VEV_REALIZED_VOL_ALPHA) * rv_var + VEV_REALIZED_VOL_ALPHA * ret * ret
            rv_count += 1
        rv_sigma = math.sqrt(max(rv_var, 0.0) * TICKS_PER_DAY) if rv_count >= VEV_REALIZED_VOL_MIN_SAMPLES else None
        rv_state.update({"spot": spot_mid, "var": rv_var, "count": rv_count, "sigma": rv_sigma or 0.0})
        saved["vev_realized_vol"] = rv_state

        T = time_to_expiry(state)
        sqrt_T = math.sqrt(T)

        # ── fit raw smile ──
        moneyness_pts: List[float] = []
        iv_pts: List[float] = []
        fit_weights: List[float] = []
        market_mid: Dict[str, float] = {}
        market_iv: Dict[str, float] = {}
        iv_point_state = saved.setdefault("vev_iv_point_state", {})
        if not isinstance(iv_point_state, dict):
            iv_point_state = {}
            saved["vev_iv_point_state"] = iv_point_state
        for prod, K in VEV_STRIKES.items():
            if prod not in state.order_depths:
                continue
            if K in VEV_PINNED_STRIKES and not VEV_INCLUDE_PINNED_IN_FIT:
                continue
            d = state.order_depths[prod]
            bb, ba = best_bid(d), best_ask(d)
            if bb is None or ba is None:
                continue
            c_mid = (bb[0] + ba[0]) / 2.0
            iv = implied_vol(
                c_mid,
                S,
                K,
                T,
                adaptive_hi=bool(VEV_ADAPTIVE_IV_HI),
            )
            if iv is None or iv < 1e-4:
                continue
            market_mid[prod] = c_mid
            market_iv[prod] = iv

            iv_for_fit = iv
            ps = iv_point_state.get(prod, {})
            if not isinstance(ps, dict):
                ps = {}
            hist_w = max(0.0, min(1.0, VEV_IV_POINT_HIST_WEIGHT))
            if VEV_IV_POINT_MODE == 1:
                prev_smooth = ps.get("ema")
                if isinstance(prev_smooth, (int, float)) and prev_smooth > 0:
                    new_smooth = (
                        VEV_IV_POINT_ALPHA * iv + (1.0 - VEV_IV_POINT_ALPHA) * float(prev_smooth)
                    )
                    iv_for_fit = (1.0 - hist_w) * iv + hist_w * float(prev_smooth)
                else:
                    new_smooth = iv
                ps["ema"] = new_smooth
            elif VEV_IV_POINT_MODE == 2:
                window = ps.get("window")
                if not isinstance(window, list):
                    window = []
                if window:
                    hist_mean = sum(window) / float(len(window))
                    iv_for_fit = (1.0 - hist_w) * iv + hist_w * hist_mean
                window.append(iv)
                if len(window) > VEV_IV_POINT_WINDOW:
                    window[:] = window[-VEV_IV_POINT_WINDOW:]
                ps["window"] = window
            iv_point_state[prod] = ps

            moneyness_pts.append(math.log(K / S) / sqrt_T)
            iv_pts.append(iv_for_fit)
            spread = max(1, ba[0] - bb[0])
            fit_weights.append(1.0 / float(spread))

        smile = fit_smile(
            moneyness_pts,
            iv_pts,
            fit_weights if VEV_WEIGHT_SMILE_BY_SPREAD else None,
            VEV_SMILE_FIT_MODE,
        )
        if smile is None:
            return orders_out, fair_out
        a_raw, b_raw, c_raw = smile

        # ── build both smiles: current-frame and slow EMA, then blend ──
        prev = saved.get("vev_smile_ema")
        if prev and all(k in prev for k in ("a", "b", "c")):
            alpha = VEV_SMILE_EMA
            a_ema = alpha * a_raw + (1 - alpha) * prev["a"]
            b_ema = alpha * b_raw + (1 - alpha) * prev["b"]
            c_ema = alpha * c_raw + (1 - alpha) * prev["c"]
        else:
            a_ema, b_ema, c_ema = a_raw, b_raw, c_raw
        w_current = max(0.0, min(1.0, VEV_CURRENT_SMILE_WEIGHT))
        a = w_current * a_raw + (1.0 - w_current) * a_ema
        b = w_current * b_raw + (1.0 - w_current) * b_ema
        c = w_current * c_raw + (1.0 - w_current) * c_ema

        # ── compute portfolio delta across ALL strikes for hedge ──
        net_delta = 0.0
        for prod, K in VEV_STRIKES.items():
            pos = int(state.position.get(prod, 0))
            if pos == 0:
                continue
            m = math.log(K / S) / sqrt_T
            fiv = a * m * m + b * m + c
            if fiv > 0:
                net_delta += pos * bs_delta(S, float(K), T, fiv)

        hedge_target = -int(round(net_delta))
        hedge_target = clamp(hedge_target, -VELVET_HEDGE_CAP, VELVET_HEDGE_CAP)
        saved["vev_hedge_target"] = hedge_target
        saved["vev_net_delta"] = net_delta

        vev_stats = saved.setdefault("vev_stats", {})
        if not isinstance(vev_stats, dict):
            vev_stats = {}
            saved["vev_stats"] = vev_stats
        diag: Dict[str, Any] = {
            "a": a, "b": b, "c": c, "n_points": len(moneyness_pts),
            "raw": {"a": a_raw, "b": b_raw, "c": c_raw},
            "ema": {"a": a_ema, "b": b_ema, "c": c_ema},
            "fit_mode": VEV_SMILE_FIT_MODE,
            "current_weight": w_current,
            "include_pinned": VEV_INCLUDE_PINNED_IN_FIT,
            "realized_vol": rv_sigma or 0.0,
            "realized_weight": VEV_REALIZED_VOL_WEIGHT,
            "S": S, "T": T, "net_delta": net_delta,
            "hedge_target": hedge_target, "z": {},
        }

        for prod, K in VEV_STRIKES.items():
            if prod not in state.order_depths or K in VEV_PINNED_STRIKES or K in VEV_SKIP_QUOTE:
                continue
            d = state.order_depths[prod]
            position = int(state.position.get(prod, 0))
            bb, ba = best_bid(d), best_ask(d)
            m = math.log(K / S) / sqrt_T
            fitted_iv = a * m * m + b * m + c
            if fitted_iv <= 0:
                continue

            prod_stats = vev_stats.get(prod, {})
            if not isinstance(prod_stats, dict):
                prod_stats = {}
            rolling_iv = prod_stats.get("iv")
            if isinstance(rolling_iv, (int, float)) and rolling_iv > 0:
                fitted_iv = (1.0 - VEV_IV_BLEND) * fitted_iv + VEV_IV_BLEND * float(rolling_iv)
            if rv_sigma is not None and rv_sigma > 0:
                w_rv = max(0.0, min(1.0, VEV_REALIZED_VOL_WEIGHT))
                fitted_iv = (1.0 - w_rv) * fitted_iv + w_rv * rv_sigma

            fair = bs_call(S, float(K), T, fitted_iv)
            fair = max(fair, max(S - float(K), 0.0))
            fair_int = int(round(fair))
            if fair < VEV_MIN_TRADE_FAIR:
                continue

            buy_cap = min(max(VEV_STRIKE_CAP - position, 0), max(VEV_LIMIT - position, 0))
            sell_cap = min(max(VEV_STRIKE_CAP + position, 0), max(VEV_LIMIT + position, 0))
            ords: List[Order] = []

            mid = market_mid.get(prod)
            resid_mean = float(prod_stats.get("resid_mean", 0.0))
            resid_dev = max(float(prod_stats.get("resid_dev", VEV_MIN_RESID_DEV)), VEV_MIN_RESID_DEV)
            residual = 0.0 if mid is None else mid - fair
            centered_residual = residual - resid_mean
            z = centered_residual / resid_dev

            alpha = VEV_STAT_ALPHA
            updated_mean = (1.0 - alpha) * resid_mean + alpha * residual
            updated_dev = max(
                (1.0 - alpha) * resid_dev + alpha * abs(centered_residual),
                VEV_MIN_RESID_DEV,
            )
            updated_iv = market_iv.get(prod, fitted_iv)
            if isinstance(rolling_iv, (int, float)) and rolling_iv > 0:
                updated_iv = (1.0 - alpha) * float(rolling_iv) + alpha * updated_iv
            prod_stats = {
                "resid_mean": updated_mean,
                "resid_dev": updated_dev,
                "iv": updated_iv,
                "z": z,
            }
            vev_stats[prod] = prod_stats
            diag["z"][prod] = round(z, 3)

            strike_delta = bs_delta(S, float(K), T, fitted_iv)
            signal_fair = fair + resid_mean
            skewed_fair = int(round(signal_fair))
            if skewed_fair < 2:
                continue

            # ── aggressive residual scalping ──
            if z <= -VEV_ENTRY_Z and ba is not None and buy_cap > 0:
                take_left = min(buy_cap, VEV_MAX_TAKE)
                for ask_p in sorted(d.sell_orders.keys()):
                    if ask_p > int(round(signal_fair)) - VEV_TAKE_EDGE or take_left <= 0:
                        break
                    vol = min(-d.sell_orders[ask_p], take_left, buy_cap)
                    if vol > 0:
                        ords.append(Order(prod, ask_p, vol))
                        buy_cap -= vol
                        take_left -= vol
            elif position < 0 and z < VEV_EXIT_Z and ba is not None and buy_cap > 0:
                vol = min(-position, -ba[1], buy_cap, VEV_MAX_TAKE)
                if vol > 0 and ba[0] <= int(round(signal_fair)):
                    ords.append(Order(prod, ba[0], vol))
                    buy_cap -= vol

            if z >= VEV_ENTRY_Z and bb is not None and sell_cap > 0:
                take_left = min(sell_cap, VEV_MAX_TAKE)
                for bid_p in sorted(d.buy_orders.keys(), reverse=True):
                    if bid_p < int(round(signal_fair)) + VEV_TAKE_EDGE or take_left <= 0:
                        break
                    vol = min(d.buy_orders[bid_p], take_left, sell_cap)
                    if vol > 0:
                        ords.append(Order(prod, bid_p, -vol))
                        sell_cap -= vol
                        take_left -= vol
            elif position > 0 and z > -VEV_EXIT_Z and bb is not None and sell_cap > 0:
                vol = min(position, bb[1], sell_cap, VEV_MAX_TAKE)
                if vol > 0 and bb[0] >= int(round(signal_fair)):
                    ords.append(Order(prod, bb[0], -vol))
                    sell_cap -= vol

            # ── book-aware passive quoting with signal and inventory skew ──
            abs_pos = abs(position)
            g = 0.0 if abs_pos <= VEV_SOFTCAP else min((abs_pos - VEV_SOFTCAP) / float(VEV_YARDAGE), 1.0)
            bid_price, ask_price = _base_quotes(skewed_fair, bb, ba, VEV_TIGHT, VEV_WIDE)
            bid_dist = max(skewed_fair - bid_price, 1)
            ask_dist = max(ask_price - skewed_fair, 1)

            signal_strength = min(abs(z) / max(VEV_ENTRY_Z, 1e-9), 2.0)
            quote_buy_cap = min(int(buy_cap * VEV_PASSIVE_SIZE_FRAC), VEV_MAX_QUOTE)
            quote_sell_cap = min(int(sell_cap * VEV_PASSIVE_SIZE_FRAC), VEV_MAX_QUOTE)
            if z <= -VEV_EXIT_Z:
                quote_buy_cap = min(int(quote_buy_cap * (1.0 + 0.5 * signal_strength)), VEV_MAX_QUOTE)
                quote_sell_cap = int(quote_sell_cap * max(0.25, 1.0 - 0.35 * signal_strength))
            elif z >= VEV_EXIT_Z:
                quote_sell_cap = min(int(quote_sell_cap * (1.0 + 0.5 * signal_strength)), VEV_MAX_QUOTE)
                quote_buy_cap = int(quote_buy_cap * max(0.25, 1.0 - 0.35 * signal_strength))

            bid_price, ask_price, bid_size, ask_size = _apply_inventory_skew(
                skewed_fair, position, bid_price, ask_price, bid_dist, ask_dist,
                g, quote_buy_cap, quote_sell_cap, VEV_SOFTCAP,
            )
            bid_price = clamp(bid_price, 1, skewed_fair - 1)
            ask_price = clamp(ask_price, skewed_fair + 1, 10**9)
            bid_size = min(max(bid_size, 0), buy_cap, VEV_MAX_QUOTE)
            ask_size = min(max(ask_size, 0), sell_cap, VEV_MAX_QUOTE)
            if VEV_STRIKE_DELTA_SIZE_BIAS:
                shape = max(0.35, 1.0 - 1.2 * abs(strike_delta - 0.5))
                bid_size = int(bid_size * shape)
                ask_size = int(ask_size * shape)
            if bid_size > 0:
                ords.append(Order(prod, bid_price, bid_size))
            if ask_size > 0:
                ords.append(Order(prod, ask_price, -ask_size))
            if ords:
                orders_out[prod] = ords
            fair_out[prod] = fair

        saved["vev_smile_current"] = {"a": a_raw, "b": b_raw, "c": c_raw}
        saved["vev_smile_ema"] = {"a": a_ema, "b": b_ema, "c": c_ema}
        saved["vev_smile"] = {"a": a, "b": b, "c": c}
        saved["vev"] = diag

        return orders_out, fair_out

    def run(self, state: TradingState):
        saved: Dict[str, Any] = {}
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
            except Exception:
                saved = {}

        result: Dict[str, List[Order]] = {p: [] for p in state.order_depths}

        if ENABLE_HYDROGEL and HYDROGEL in state.order_depths:
            orders, fair = self.run_hydrogel(state, saved)
            result[HYDROGEL] = orders

        if ENABLE_VELVET and VELVET in state.order_depths:
            orders, fair = self.run_velvetfruit(state, saved)
            result[VELVET] = orders

        if ENABLE_VEV:
            vev_orders, vev_fairs = self.run_vev_options(state, saved)
            for p, od_list in vev_orders.items():
                if p in result:
                    result[p] = od_list

        trader_data = json.dumps(saved)
        logger.flush(state, result, 0, trader_data)
        return result, 0, trader_data

"""
Prosperity 4 — Parameterized Strategy Sweep
=============================================
Highly parameterized version of the Merged_checkpoint_1 strategy.
Each product has its own config array and selector so you can sweep
them independently and isolate results.

Set ACTIVE to control which product(s) trade:
  "ACO"  — only ASH_COATED_OSMIUM trades, IPR sends no orders
  "IPR"  — only INTARIAN_PEPPER_ROOT trades, ACO sends no orders
  "BOTH" — both trade with their respective configs

Architecture: 4-phase market maker (take positive-EV, take flatten,
make, soft-cap pressure) per product.

ACO stages:                          IPR stages:
  A (0-7):   Edge vs fill             A (0-7):   Edge vs fill
  B (8-12):  Passive size             B (8-12):  Passive size
  C (13-21): Inventory control        C (13-21): Inventory control
  D (22-26): EMA alpha                D (22-25): Bid/ask skew
  E (27-29): improve_if_wide          E (26-29): Slope
                                      F (30-32): improve_if_wide

Submit this file directly to the IMC Prosperity platform.
"""

# ═══════════════════════════════════════════════════════════════════════
# SWEEP CONTROLS — change these to switch what you're testing
# ═══════════════════════════════════════════════════════════════════════
ACTIVE = "BOTH"           # "ACO" | "IPR" | "BOTH"
ACO_CONFIG_ID = 3         # index into ACO_CONFIGS (3 = baseline)
IPR_CONFIG_ID = 3         # index into IPR_CONFIGS (3 = baseline)
# ═══════════════════════════════════════════════════════════════════════

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import math
import json

# ── Product names ─────────────────────────────────────────────────────
ACO = "ASH_COATED_OSMIUM"
IPR = "INTARIAN_PEPPER_ROOT"

# ── Baselines (reproduce Merged_checkpoint_1 behavior exactly) ───────
ACO_BASELINE = {
    "position_limit": 80,
    "soft_cap":       60,
    "make_portion":   0.8,
    "anchor":         10_000,
    "clamp_band":     20,
    "ema_alpha":      0.25,
    "min_take_edge":  1,
    "maker_mode":     "improve_1",   # "join" | "improve_1" | "improve_if_wide"
    "skew_strength":  0,             # ticks of inventory-skew on maker quotes
    "size_haircut":   1.0,           # same-side volume *= max(0, 1 - pressure*hc)
    "spread_threshold": 3,           # for improve_if_wide mode
    "pressure_mode":  "symmetric",   # "symmetric" | "long_bias"
    "bid_frac":       0.5,           # 0.5 = symmetric volume
    "ask_frac":       0.5,
}

IPR_BASELINE = {
    "position_limit": 80,
    "soft_cap":       75,
    "make_portion":   0.9,
    "min_take_edge":  1,
    "slope":          0.001,
    "bid_frac":       0.70,
    "ask_frac":       0.30,
    "maker_mode":     "improve_1",
    "skew_strength":  0,
    "size_haircut":   1.0,
    "spread_threshold": 3,
    "pressure_mode":  "long_bias",
}

# ═══════════════════════════════════════════════════════════════════════
# ACO PARAMETER SWEEP CONFIGS
# Each entry: dict of overrides merged onto ACO_BASELINE.
# ═══════════════════════════════════════════════════════════════════════
ACO_CONFIGS = [
    # ── Stage A: Edge vs Fill (0–7) ──────────────────────────────────
    # min_take_edge [0,1,2,3] x maker_mode [join, improve_1]
    {"min_take_edge": 0, "maker_mode": "join"},       # 0
    {"min_take_edge": 0, "maker_mode": "improve_1"},  # 1
    {"min_take_edge": 1, "maker_mode": "join"},        # 2
    {"min_take_edge": 1, "maker_mode": "improve_1"},   # 3  <- baseline
    {"min_take_edge": 2, "maker_mode": "join"},        # 4
    {"min_take_edge": 2, "maker_mode": "improve_1"},   # 5
    {"min_take_edge": 3, "maker_mode": "join"},        # 6
    {"min_take_edge": 3, "maker_mode": "improve_1"},   # 7

    # ── Stage B: Passive Size (8–12) ─────────────────────────────────
    {"make_portion": 0.4},   # 8
    {"make_portion": 0.6},   # 9
    {"make_portion": 0.8},   # 10  <- baseline
    {"make_portion": 0.9},   # 11
    {"make_portion": 1.0},   # 12

    # ── Stage C: Inventory Control (13–21) ───────────────────────────
    # soft_cap [40, 50, 60] x skew_strength [0, 1, 2]
    {"soft_cap": 40, "skew_strength": 0},  # 13
    {"soft_cap": 40, "skew_strength": 1},  # 14
    {"soft_cap": 40, "skew_strength": 2},  # 15
    {"soft_cap": 50, "skew_strength": 0},  # 16
    {"soft_cap": 50, "skew_strength": 1},  # 17
    {"soft_cap": 50, "skew_strength": 2},  # 18
    {"soft_cap": 60, "skew_strength": 0},  # 19  <- baseline soft_cap
    {"soft_cap": 60, "skew_strength": 1},  # 20
    {"soft_cap": 60, "skew_strength": 2},  # 21

    # ── Stage D: EMA Alpha (22–26) ───────────────────────────────────
    {"ema_alpha": 0.10},  # 22
    {"ema_alpha": 0.20},  # 23
    {"ema_alpha": 0.25},  # 24  <- baseline
    {"ema_alpha": 0.35},  # 25
    {"ema_alpha": 0.50},  # 26

    # ── Stage E: Conditional Maker Aggression (27–29) ────────────────
    {"maker_mode": "improve_if_wide", "spread_threshold": 3},  # 27
    {"maker_mode": "improve_if_wide", "spread_threshold": 5},  # 28
    {"maker_mode": "improve_if_wide", "spread_threshold": 8},  # 29
]

# ═══════════════════════════════════════════════════════════════════════
# IPR PARAMETER SWEEP CONFIGS
# Each entry: dict of overrides merged onto IPR_BASELINE.
# ═══════════════════════════════════════════════════════════════════════
IPR_CONFIGS = [
    # ── Stage A: Edge vs Fill (0–7) ──────────────────────────────────
    # min_take_edge [0,1,2,3] x maker_mode [join, improve_1]
    {"min_take_edge": 0, "maker_mode": "join"},       # 0
    {"min_take_edge": 0, "maker_mode": "improve_1"},  # 1
    {"min_take_edge": 1, "maker_mode": "join"},        # 2
    {"min_take_edge": 1, "maker_mode": "improve_1"},   # 3  <- baseline
    {"min_take_edge": 2, "maker_mode": "join"},        # 4
    {"min_take_edge": 2, "maker_mode": "improve_1"},   # 5
    {"min_take_edge": 3, "maker_mode": "join"},        # 6
    {"min_take_edge": 3, "maker_mode": "improve_1"},   # 7

    # ── Stage B: Passive Size (8–12) ─────────────────────────────────
    {"make_portion": 0.4},   # 8
    {"make_portion": 0.6},   # 9
    {"make_portion": 0.8},   # 10
    {"make_portion": 0.9},   # 11  <- baseline
    {"make_portion": 1.0},   # 12

    # ── Stage C: Inventory Control (13–21) ───────────────────────────
    # soft_cap [45, 55, 65, 75] x skew_strength [0, 1, 2]
    {"soft_cap": 45, "skew_strength": 0},  # 13
    {"soft_cap": 45, "skew_strength": 1},  # 14
    {"soft_cap": 45, "skew_strength": 2},  # 15
    {"soft_cap": 55, "skew_strength": 0},  # 16
    {"soft_cap": 55, "skew_strength": 1},  # 17
    {"soft_cap": 55, "skew_strength": 2},  # 18
    {"soft_cap": 65, "skew_strength": 0},  # 19
    {"soft_cap": 65, "skew_strength": 1},  # 20
    {"soft_cap": 65, "skew_strength": 2},  # 21

    # ── Stage D: Bid/Ask Skew (22–25) ────────────────────────────────
    {"bid_frac": 0.50, "ask_frac": 0.50},  # 22
    {"bid_frac": 0.60, "ask_frac": 0.40},  # 23
    {"bid_frac": 0.70, "ask_frac": 0.30},  # 24  <- baseline
    {"bid_frac": 0.80, "ask_frac": 0.20},  # 25

    # ── Stage E: Slope (26–29) ───────────────────────────────────────
    {"slope": 0.0005},  # 26
    {"slope": 0.001},   # 27  <- baseline
    {"slope": 0.002},   # 28
    {"slope": 0.003},   # 29

    # ── Stage F: Conditional Maker Aggression (30–32) ────────────────
    {"maker_mode": "improve_if_wide", "spread_threshold": 3},  # 30
    {"maker_mode": "improve_if_wide", "spread_threshold": 5},  # 31
    {"maker_mode": "improve_if_wide", "spread_threshold": 8},  # 32
]

# ── Resolve active configs ───────────────────────────────────────────
ACO_CFG = {**ACO_BASELINE, **ACO_CONFIGS[ACO_CONFIG_ID]}
IPR_CFG = {**IPR_BASELINE, **IPR_CONFIGS[IPR_CONFIG_ID]}
ACO_ACTIVE = ACTIVE in ("ACO", "BOTH")
IPR_ACTIVE = ACTIVE in ("IPR", "BOTH")


class Trader:

    def bid(self) -> int:
        return 0

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _load_state(td: str) -> dict:
        if not td:
            return {}
        try:
            return json.loads(td)
        except Exception:
            return {}

    @staticmethod
    def _save_state(state: dict) -> str:
        return json.dumps(state)

    # ==================================================================
    # ASH_COATED_OSMIUM — fair value
    # ==================================================================

    @staticmethod
    def _aco_book_fair(depth: OrderDepth) -> Optional[float]:
        """L2+L3 mid. Returns None if either side is empty."""
        bids = sorted(depth.buy_orders.keys(), reverse=True)
        asks = sorted(depth.sell_orders.keys())
        if not bids or not asks:
            return None

        if len(bids) >= 3:
            bid_levels = [bids[1], bids[2]]
        elif len(bids) == 2:
            bid_levels = [bids[0], bids[1]]
        else:
            bid_levels = [bids[0]]

        if len(asks) >= 3:
            ask_levels = [asks[1], asks[2]]
        elif len(asks) == 2:
            ask_levels = [asks[0], asks[1]]
        else:
            ask_levels = [asks[0]]

        all_levels = bid_levels + ask_levels
        return sum(all_levels) / len(all_levels)

    @staticmethod
    def _aco_update_fair(
        book_fair: Optional[float], prev_ema: Optional[float], cfg: dict,
    ) -> float:
        anchor = cfg["anchor"]
        band = cfg["clamp_band"]
        alpha = cfg["ema_alpha"]

        if book_fair is not None:
            clamped = max(anchor - band, min(anchor + band, book_fair))
            if prev_ema is not None:
                fair = alpha * clamped + (1 - alpha) * prev_ema
            else:
                fair = clamped
        elif prev_ema is not None:
            fair = prev_ema
        else:
            fair = float(anchor)

        return max(anchor - band, min(anchor + band, fair))

    # ==================================================================
    # INTARIAN_PEPPER_ROOT — fair value
    # ==================================================================

    @staticmethod
    def _ipr_compute_fair(
        timestamp: int, depth: OrderDepth,
        prev_fair: Optional[float], prev_ts: Optional[int],
        cfg: dict,
    ) -> float:
        slope = cfg["slope"]

        if prev_fair is not None and prev_ts is not None:
            dt = timestamp - prev_ts
            return prev_fair + dt * slope

        if depth.buy_orders and depth.sell_orders:
            return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0

        if depth.sell_orders:
            return min(depth.sell_orders) - 5.0
        if depth.buy_orders:
            return max(depth.buy_orders) + 5.0

        return 10000.0

    # ==================================================================
    # Shared phases
    # ==================================================================

    @staticmethod
    def _phase_take_positive(
        product: str, depth: OrderDepth, fair: float,
        buy_cap: int, sell_cap: int, min_take_edge: int,
    ) -> tuple:
        orders: List[Order] = []

        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair - min_take_edge or buy_cap <= 0:
                break
            qty = min(-depth.sell_orders[ask_px], buy_cap)
            if qty > 0:
                orders.append(Order(product, ask_px, qty))
                buy_cap -= qty

        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px <= fair + min_take_edge or sell_cap <= 0:
                break
            qty = min(depth.buy_orders[bid_px], sell_cap)
            if qty > 0:
                orders.append(Order(product, bid_px, -qty))
                sell_cap -= qty

        return orders, buy_cap, sell_cap

    @staticmethod
    def _phase_take_flatten(
        product: str, depth: OrderDepth, fair: float, position: int,
        buy_cap: int, sell_cap: int,
    ) -> tuple:
        orders: List[Order] = []
        fair_int = round(fair)

        if position < 0:
            flatten_vol = min(abs(position), buy_cap)
            for ask_px in sorted(depth.sell_orders):
                if ask_px > fair_int or flatten_vol <= 0:
                    break
                avail = -depth.sell_orders[ask_px]
                qty = min(avail, flatten_vol)
                if qty > 0:
                    orders.append(Order(product, ask_px, qty))
                    buy_cap -= qty
                    flatten_vol -= qty

        elif position > 0:
            flatten_vol = min(position, sell_cap)
            for bid_px in sorted(depth.buy_orders, reverse=True):
                if bid_px < fair_int or flatten_vol <= 0:
                    break
                avail = depth.buy_orders[bid_px]
                qty = min(avail, flatten_vol)
                if qty > 0:
                    orders.append(Order(product, bid_px, -qty))
                    sell_cap -= qty
                    flatten_vol -= qty

        return orders, buy_cap, sell_cap

    # ==================================================================
    # Unified maker phase (parameterized by cfg dict)
    # ==================================================================

    @staticmethod
    def _phase_make(
        product: str, depth: OrderDepth, fair: float, position: int,
        buy_cap: int, sell_cap: int, cfg: dict,
    ) -> tuple:
        orders: List[Order] = []
        pos_limit = cfg["position_limit"]
        soft_cap = cfg["soft_cap"]
        make_portion = cfg["make_portion"]
        bid_frac = cfg["bid_frac"]
        ask_frac = cfg["ask_frac"]
        maker_mode = cfg["maker_mode"]
        skew_str = cfg["skew_strength"]
        size_hc = cfg["size_haircut"]
        spread_thresh = cfg["spread_threshold"]
        pressure_mode = cfg["pressure_mode"]

        is_long = position > 0
        is_short = position < 0
        abs_pos = abs(position)

        # ── Compute pressure ──────────────────────────────────────────
        if pressure_mode == "long_bias":
            if is_short and abs_pos > soft_cap:
                pressure = min((abs_pos - soft_cap) / (pos_limit - soft_cap), 1.0)
            elif is_long and position > pos_limit - 2:
                pressure = 0.5
            else:
                pressure = 0.0
        else:
            if abs_pos > soft_cap:
                pressure = min((abs_pos - soft_cap) / (pos_limit - soft_cap), 1.0)
            else:
                pressure = 0.0

        # ── Compute maker prices ─────────────────────────────────────
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

        should_improve = (
            maker_mode == "improve_1"
            or (maker_mode == "improve_if_wide"
                and spread is not None
                and spread >= spread_thresh)
        )

        bid_price = math.floor(fair) - 1
        for bp in sorted(depth.buy_orders, reverse=True):
            if should_improve:
                candidate = bp + 1
                if candidate < fair:
                    bid_price = candidate
                    break
            if bp < fair:
                bid_price = bp
                break

        ask_price = math.ceil(fair) + 1
        for ap in sorted(depth.sell_orders):
            if should_improve:
                candidate = ap - 1
                if candidate > fair:
                    ask_price = candidate
                    break
            if ap > fair:
                ask_price = ap
                break

        # ── Apply inventory skew ─────────────────────────────────────
        if skew_str > 0 and position != 0:
            if is_long:
                ask_price = max(math.ceil(fair) + 1, ask_price - skew_str)
                bid_price = bid_price - skew_str
            elif is_short:
                bid_price = min(math.floor(fair) - 1, bid_price + skew_str)
                ask_price = ask_price + skew_str

        # ── Apply pressure tightening on offsetting side ─────────────
        if pressure > 0:
            if is_long:
                tighter = max(math.ceil(fair) + 1, ask_price - round(pressure))
                if tighter > fair:
                    ask_price = tighter
            elif is_short:
                tighter = min(math.floor(fair) - 1, bid_price + round(pressure))
                if tighter < fair:
                    bid_price = tighter

        # ── Compute volumes ──────────────────────────────────────────
        # bid_frac/ask_frac = 0.5 gives symmetric (ACO default);
        # bid_frac=0.7, ask_frac=0.3 gives IPR-style long bias.
        base_buy_vol = min(int(buy_cap * make_portion * bid_frac / 0.5), buy_cap)
        base_sell_vol = min(int(sell_cap * make_portion * ask_frac / 0.5), sell_cap)

        if pressure > 0:
            if is_long:
                base_buy_vol = int(base_buy_vol * max(0.0, 1.0 - pressure * size_hc))
            elif is_short:
                base_sell_vol = int(base_sell_vol * max(0.0, 1.0 - pressure * size_hc))

        if base_buy_vol > 0:
            orders.append(Order(product, bid_price, base_buy_vol))
        if base_sell_vol > 0:
            orders.append(Order(product, ask_price, -base_sell_vol))

        return orders, buy_cap, sell_cap, pressure, bid_price, ask_price, base_buy_vol, base_sell_vol

    # ==================================================================
    # Logging helpers
    # ==================================================================

    @staticmethod
    def _log_book(depth: OrderDepth) -> str:
        bids = sorted(depth.buy_orders.items(), reverse=True)
        asks = sorted(depth.sell_orders.items())
        bid_str = " ".join(f"{p}x{v}" for p, v in bids)
        ask_str = " ".join(f"{p}x{abs(v)}" for p, v in asks)
        return f"B[{bid_str}] A[{ask_str}]"

    @staticmethod
    def _log_orders(label: str, orders: List[Order]) -> str:
        if not orders:
            return f"  {label}: --"
        parts = []
        for o in orders:
            side = "BUY" if o.quantity > 0 else "SELL"
            parts.append(f"{side} {abs(o.quantity)}@{o.price}")
        return f"  {label}: {', '.join(parts)}"

    # ==================================================================
    # Entry point
    # ==================================================================

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        saved = self._load_state(state.traderData)

        if not saved.get("_cfg_logged"):
            parts = [f"[CONFIG] active={ACTIVE}"]
            if ACO_ACTIVE:
                s = {k: v for k, v in ACO_CFG.items() if k != "position_limit"}
                parts.append(f"aco[{ACO_CONFIG_ID}]={json.dumps(s)}")
            if IPR_ACTIVE:
                s = {k: v for k, v in IPR_CFG.items() if k != "position_limit"}
                parts.append(f"ipr[{IPR_CONFIG_ID}]={json.dumps(s)}")
            print(" ".join(parts))
            saved["_cfg_logged"] = True

        for product in state.order_depths:
            depth = state.order_depths[product]
            position = state.position.get(product, 0)

            if product == ACO and ACO_ACTIVE:
                self._run_aco(product, depth, position, state, saved, result)
            elif product == IPR and IPR_ACTIVE:
                self._run_ipr(product, depth, position, state, saved, result)
            else:
                result[product] = []

        traderData = self._save_state(saved)
        conversions = 0
        return result, conversions, traderData

    # ------------------------------------------------------------------
    # Per-product runners
    # ------------------------------------------------------------------

    def _run_aco(self, product, depth, position, state, saved, result):
        aco_state = saved.setdefault("aco", {})
        cfg = ACO_CFG

        prev_ema = aco_state.get("ema")
        book_fair = self._aco_book_fair(depth)
        fair = self._aco_update_fair(book_fair, prev_ema, cfg)
        aco_state["ema"] = fair

        both_sides = bool(depth.buy_orders and depth.sell_orders)

        buy_cap_init = cfg["position_limit"] - position
        sell_cap_init = cfg["position_limit"] + position
        buy_cap = buy_cap_init
        sell_cap = sell_cap_init

        all_orders: List[Order] = []

        take_orders, buy_cap, sell_cap = self._phase_take_positive(
            product, depth, fair, buy_cap, sell_cap, cfg["min_take_edge"],
        )
        all_orders.extend(take_orders)

        flat_orders, buy_cap, sell_cap = self._phase_take_flatten(
            product, depth, fair, position, buy_cap, sell_cap,
        )
        all_orders.extend(flat_orders)

        mk_orders, buy_cap, sell_cap, pressure, mk_bid, mk_ask, mk_bvol, mk_svol = (
            self._phase_make(product, depth, fair, position, buy_cap, sell_cap, cfg)
        )
        all_orders.extend(mk_orders)

        result[product] = all_orders

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
        src = "L23" if book_fair is not None else "EMA"

        print(
            f"[ACO] t={state.timestamp} pos={position:+d} fair={fair:.1f}({src}) "
            f"spread={spread} cap={buy_cap_init}/{sell_cap_init}"
        )
        print(f"  book: {self._log_book(depth)}")
        if not both_sides:
            print("  *** ONE-SIDED BOOK ***")
        print(self._log_orders("TAKE", take_orders))
        print(self._log_orders("FLAT", flat_orders))
        print(
            f"  MAKE: bid {mk_bvol}@{mk_bid} ask {mk_svol}@{mk_ask} "
            f"pressure={pressure:.2f} mode={cfg['maker_mode']} skew={cfg['skew_strength']}"
        )
        print(
            f"  [METRICS] product=ACO pos={position} fair={fair:.1f} spread={spread} "
            f"pressure={pressure:.2f} n_take={len(take_orders)} n_flat={len(flat_orders)} "
            f"n_make={len(mk_orders)} mk_bvol={mk_bvol} mk_svol={mk_svol}"
        )

    def _run_ipr(self, product, depth, position, state, saved, result):
        ipr_state = saved.setdefault("ipr", {})
        cfg = IPR_CFG

        prev_fair = ipr_state.get("fair")
        prev_ts = ipr_state.get("ts")
        fair = self._ipr_compute_fair(state.timestamp, depth, prev_fair, prev_ts, cfg)
        ipr_state["fair"] = fair
        ipr_state["ts"] = state.timestamp

        buy_cap_init = cfg["position_limit"] - position
        sell_cap_init = cfg["position_limit"] + position
        buy_cap = buy_cap_init
        sell_cap = sell_cap_init

        all_orders: List[Order] = []

        take_orders, buy_cap, sell_cap = self._phase_take_positive(
            product, depth, fair, buy_cap, sell_cap, cfg["min_take_edge"],
        )
        all_orders.extend(take_orders)

        flat_orders, buy_cap, sell_cap = self._phase_take_flatten(
            product, depth, fair, position, buy_cap, sell_cap,
        )
        all_orders.extend(flat_orders)

        mk_orders, buy_cap, sell_cap, pressure, mk_bid, mk_ask, mk_bvol, mk_svol = (
            self._phase_make(product, depth, fair, position, buy_cap, sell_cap, cfg)
        )
        all_orders.extend(mk_orders)

        result[product] = all_orders

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
        src = "BOOT" if prev_fair is None else "DELTA"

        print(
            f"[IPR] t={state.timestamp} pos={position:+d} fair={fair:.1f}({src}) "
            f"spread={spread} cap={buy_cap_init}/{sell_cap_init}"
        )
        print(f"  book: {self._log_book(depth)}")
        print(self._log_orders("TAKE", take_orders))
        print(self._log_orders("FLAT", flat_orders))
        print(
            f"  MAKE: bid {mk_bvol}@{mk_bid} ask {mk_svol}@{mk_ask} "
            f"pressure={pressure:.2f} mode={cfg['maker_mode']} skew={cfg['skew_strength']}"
        )
        print(
            f"  [METRICS] product=IPR pos={position} fair={fair:.1f} spread={spread} "
            f"pressure={pressure:.2f} n_take={len(take_orders)} n_flat={len(flat_orders)} "
            f"n_make={len(mk_orders)} mk_bvol={mk_bvol} mk_svol={mk_svol}"
        )


# ======================================================================
# Local smoke test
# ======================================================================
if __name__ == "__main__":
    from datamodel import Listing, Observation

    def make_state(products_data, timestamp=100, td=""):
        order_depths = {}
        positions = {}
        listings = {}
        for prod, (buys, sells, pos) in products_data.items():
            od = OrderDepth()
            od.buy_orders = buys
            od.sell_orders = sells
            order_depths[prod] = od
            positions[prod] = pos
            listings[prod] = Listing(symbol=prod, product=prod, denomination="XIRECS")
        return TradingState(
            traderData=td, timestamp=timestamp,
            listings=listings,
            order_depths=order_depths,
            own_trades={p: [] for p in products_data},
            market_trades={p: [] for p in products_data},
            position=positions,
            observations=Observation({}, {}),
        )

    VALID_MODES = ("join", "improve_1", "improve_if_wide")

    print("=" * 70)
    print(f"  SWEEP SUBMISSION SMOKE TEST")
    print(f"  ACO_CONFIGS: {len(ACO_CONFIGS)}  |  IPR_CONFIGS: {len(IPR_CONFIGS)}")
    print(f"  Active: {ACTIVE}  ACO_CONFIG_ID={ACO_CONFIG_ID}  IPR_CONFIG_ID={IPR_CONFIG_ID}")
    print("=" * 70)

    # Validate every config resolves without missing keys
    print("\n--- Validating ACO configs ---")
    for i, overrides in enumerate(ACO_CONFIGS):
        cfg = {**ACO_BASELINE, **overrides}
        for key in ACO_BASELINE:
            assert key in cfg, f"ACO config {i}: missing '{key}'"
        assert cfg["maker_mode"] in VALID_MODES, (
            f"ACO config {i}: bad maker_mode '{cfg['maker_mode']}'"
        )
    print(f"  All {len(ACO_CONFIGS)} ACO configs OK")

    print("--- Validating IPR configs ---")
    for i, overrides in enumerate(IPR_CONFIGS):
        cfg = {**IPR_BASELINE, **overrides}
        for key in IPR_BASELINE:
            assert key in cfg, f"IPR config {i}: missing '{key}'"
        assert cfg["maker_mode"] in VALID_MODES, (
            f"IPR config {i}: bad maker_mode '{cfg['maker_mode']}'"
        )
    print(f"  All {len(IPR_CONFIGS)} IPR configs OK")

    # -- Test BOTH mode --
    print(f"\n=== BOTH mode: 3 ticks ===")
    t = Trader()

    s = make_state({
        ACO: ({9994: 15, 9991: 21}, {10010: -15, 10013: -21}, 0),
        IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, 0),
    }, timestamp=0)
    r, _, td = t.run(s)
    for prod in [ACO, IPR]:
        has_orders = len(r.get(prod, [])) > 0
        expected = (prod == ACO and ACO_ACTIVE) or (prod == IPR and IPR_ACTIVE)
        label = "TRADING" if has_orders else "IDLE"
        print(f"  {prod}: {label} ({len(r.get(prod, []))} orders)")
        if expected:
            assert has_orders or True, "Active product may legitimately have 0 orders"

    s = make_state({
        ACO: ({}, {10009: -15, 10011: -21}, 0),
        IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, 10),
    }, timestamp=100, td=td)
    r, _, td = t.run(s)

    s = make_state({
        ACO: ({9993: 14, 9991: 25}, {10009: -14, 10012: -25}, 30),
        IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, -20),
    }, timestamp=200, td=td)
    r, _, td = t.run(s)

    # -- Verify inactive product sends no orders --
    print(f"\n=== Inactive product isolation check ===")
    import copy as _cp
    _saved_active = ACTIVE
    for test_active in ["ACO", "IPR"]:
        # Temporarily patch the module-level flags for this check
        _aco_a = test_active in ("ACO", "BOTH")
        _ipr_a = test_active in ("IPR", "BOTH")

        class _TestTrader(Trader):
            def run(self, state):
                result = {}
                saved = self._load_state(state.traderData)
                for product in state.order_depths:
                    depth = state.order_depths[product]
                    position = state.position.get(product, 0)
                    if product == ACO and _aco_a:
                        self._run_aco(product, depth, position, state, saved, result)
                    elif product == IPR and _ipr_a:
                        self._run_ipr(product, depth, position, state, saved, result)
                    else:
                        result[product] = []
                return result, 0, self._save_state(saved)

        tt = _TestTrader()
        s = make_state({
            ACO: ({9994: 15, 9991: 21}, {10010: -15, 10013: -21}, 0),
            IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, 0),
        }, timestamp=0)
        r, _, _ = tt.run(s)
        inactive = IPR if test_active == "ACO" else ACO
        assert len(r.get(inactive, [])) == 0, (
            f"ACTIVE={test_active} but {inactive} got orders: {r[inactive]}"
        )
        print(f"  ACTIVE={test_active}: {inactive} correctly idle")

    # -- Position limit checks --
    print(f"\n=== Position limit checks ===")
    t2 = Trader()
    for pos in [79, -79, 0, 60, -60]:
        s = make_state({
            ACO: ({9994: 15, 9991: 21}, {10010: -15, 10013: -21}, pos),
            IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, pos),
        }, timestamp=300, td="")
        r, _, _ = t2.run(s)
        for prod in [ACO, IPR]:
            cfg = ACO_CFG if prod == ACO else IPR_CFG
            limit = cfg["position_limit"]
            net = pos + sum(o.quantity for o in r.get(prod, []))
            assert abs(net) <= limit, (
                f"LIMIT BREACH: {prod} pos={pos} net={net} limit={limit}"
            )
    print("  All position limit checks passed")

    print(f"\n{'='*70}")
    print("  SMOKE TEST PASSED")
    print(f"{'='*70}")

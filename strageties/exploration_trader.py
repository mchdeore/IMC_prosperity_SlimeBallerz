"""
Prosperity 4 — Exploration trader (parameterized, env-JSON driven)
===================================================================

Same 4-phase strategy as sweep_submission.py, but:

1. Configs come from environment variables as JSON dicts (no list-index).
   * EXPL_ACO_CFG  — full ACO config dict (merged onto ACO_BASELINE).
   * EXPL_IPR_CFG  — full IPR config dict (merged onto IPR_BASELINE).
   * EXPL_ACTIVE   — "ACO" | "IPR" | "BOTH"  (default BOTH).
   * EXPL_VERBOSE  — "1" to keep per-tick prints, "0" (default) to silence.

2. Extended maker-quote placement:
   * make_offset (int, optional) — if present, dominates maker_mode:
        bid = clamp(best_bid + make_offset,  < fair)
        ask = clamp(best_ask - make_offset,  > fair)
     make_offset=0 -> join, 1 -> improve_1, 2 -> improve_2, -1 -> back off 1 tick, ...
     Clamped so the maker quote never crosses fair.

3. IPR-only extras:
   * position_target (int, default 0) — inventory skew / pressure center.
     With position_target=+40, the strategy is symmetric around +40 lots.
   * long_take_edge (int, optional) — if set, overrides min_take_edge ONLY for
     asks (buy side). Makes the taker more aggressive on the long side.

All existing sweep_submission keys still work.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import json
import math
import os


ACO = "ASH_COATED_OSMIUM"
IPR = "INTARIAN_PEPPER_ROOT"


ACO_BASELINE = {
    "position_limit": 80,
    "soft_cap":       60,
    "make_portion":   0.8,
    "anchor":         10_000,
    "clamp_band":     20,
    "ema_alpha":      0.25,
    "min_take_edge":  1,
    "maker_mode":     "improve_1",
    "skew_strength":  0,
    "size_haircut":   1.0,
    "spread_threshold": 3,
    "pressure_mode":  "symmetric",
    "bid_frac":       0.5,
    "ask_frac":       0.5,
    "quote_bias_ticks": 0,
    "make_offset":    None,
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
    "quote_bias_ticks": 0,
    "make_offset":    None,
    "position_target": 0,
    "long_take_edge":  None,
}


def _read_cfg(env_name: str, baseline: dict) -> dict:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return dict(baseline)
    try:
        over = json.loads(raw)
    except Exception as e:
        raise ValueError(f"{env_name} is not valid JSON: {e}") from e
    if not isinstance(over, dict):
        raise ValueError(f"{env_name} must be a JSON object")
    return {**baseline, **over}


ACTIVE = os.environ.get("EXPL_ACTIVE", "BOTH").strip().upper() or "BOTH"
if ACTIVE not in ("ACO", "IPR", "BOTH"):
    raise ValueError(f"EXPL_ACTIVE must be ACO, IPR, or BOTH, got {ACTIVE!r}")

VERBOSE = os.environ.get("EXPL_VERBOSE", "0").strip() == "1"

ACO_CFG = _read_cfg("EXPL_ACO_CFG", ACO_BASELINE)
IPR_CFG = _read_cfg("EXPL_IPR_CFG", IPR_BASELINE)
ACO_ACTIVE = ACTIVE in ("ACO", "BOTH")
IPR_ACTIVE = ACTIVE in ("IPR", "BOTH")


def _vprint(*args, **kwargs) -> None:
    if VERBOSE:
        print(*args, **kwargs)


class Trader:

    def bid(self) -> int:
        return 0

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
    # Fair-value helpers
    # ==================================================================

    @staticmethod
    def _aco_book_fair(depth: OrderDepth) -> Optional[float]:
        if not depth.buy_orders or not depth.sell_orders:
            return None
        bids = sorted(depth.buy_orders.items(), reverse=True)[:3]
        asks = sorted(depth.sell_orders.items())[:3]
        if not bids or not asks:
            return None
        bvol = sum(v for _, v in bids)
        avol = sum(abs(v) for _, v in asks)
        if bvol == 0 or avol == 0:
            return None
        bmid = sum(p * v for p, v in bids) / bvol
        amid = sum(p * abs(v) for p, v in asks) / avol
        return (bmid + amid) / 2.0

    @staticmethod
    def _aco_update_fair(book_fair: Optional[float], prev_ema: Optional[float],
                         cfg: dict) -> float:
        anchor = cfg["anchor"]
        band = cfg["clamp_band"]
        alpha = cfg["ema_alpha"]

        if book_fair is not None:
            if prev_ema is None:
                fair = book_fair
            else:
                fair = alpha * book_fair + (1.0 - alpha) * prev_ema
        elif prev_ema is not None:
            fair = prev_ema
        else:
            fair = float(anchor)

        return max(anchor - band, min(anchor + band, fair))

    @staticmethod
    def _ipr_compute_fair(timestamp: int, depth: OrderDepth,
                          prev_fair: Optional[float], prev_ts: Optional[int],
                          cfg: dict) -> float:
        slope = cfg["slope"]
        if prev_fair is not None and prev_ts is not None:
            return prev_fair + (timestamp - prev_ts) * slope
        if depth.buy_orders and depth.sell_orders:
            return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
        if depth.sell_orders:
            return min(depth.sell_orders) - 5.0
        if depth.buy_orders:
            return max(depth.buy_orders) + 5.0
        return 10000.0

    # ==================================================================
    # Taker phases
    # ==================================================================

    @staticmethod
    def _phase_take_positive(
        product: str, depth: OrderDepth, fair: float,
        buy_cap: int, sell_cap: int, min_take_edge: int,
        long_take_edge: Optional[int] = None,
    ) -> tuple:
        orders: List[Order] = []
        take_asks_edge = long_take_edge if long_take_edge is not None else min_take_edge

        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair - take_asks_edge or buy_cap <= 0:
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
        position_target: int = 0,
    ) -> tuple:
        """Flatten position toward position_target (default 0)."""
        orders: List[Order] = []
        fair_int = round(fair)
        delta = position - position_target  # want to reduce |delta|

        if delta < 0:
            flatten_vol = min(abs(delta), buy_cap)
            for ask_px in sorted(depth.sell_orders):
                if ask_px > fair_int or flatten_vol <= 0:
                    break
                avail = -depth.sell_orders[ask_px]
                qty = min(avail, flatten_vol)
                if qty > 0:
                    orders.append(Order(product, ask_px, qty))
                    buy_cap -= qty
                    flatten_vol -= qty
        elif delta > 0:
            flatten_vol = min(delta, sell_cap)
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
    # Maker phase with optional integer make_offset
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
        make_offset = cfg.get("make_offset")  # None | int
        position_target = int(cfg.get("position_target", 0))

        # Pressure uses distance from target
        dev = position - position_target
        is_long_dev = dev > 0
        is_short_dev = dev < 0
        abs_dev = abs(dev)

        if pressure_mode == "long_bias":
            if is_short_dev and abs_dev > soft_cap:
                pressure = min((abs_dev - soft_cap) / (pos_limit - soft_cap), 1.0)
            elif is_long_dev and position > pos_limit - 2:
                pressure = 0.5
            else:
                pressure = 0.0
        else:
            if abs_dev > soft_cap:
                pressure = min((abs_dev - soft_cap) / (pos_limit - soft_cap), 1.0)
            else:
                pressure = 0.0

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

        # ── Price selection ─────────────────────────────────────────
        if make_offset is not None:
            # Integer offset mode: bid = best_bid + offset (must stay < fair),
            # ask = best_ask - offset (must stay > fair).
            if best_bid is not None:
                raw_bid = best_bid + int(make_offset)
                # Clamp strictly below fair
                bid_price = min(raw_bid, math.floor(fair) - 1)
            else:
                bid_price = math.floor(fair) - 1

            if best_ask is not None:
                raw_ask = best_ask - int(make_offset)
                ask_price = max(raw_ask, math.ceil(fair) + 1)
            else:
                ask_price = math.ceil(fair) + 1
        else:
            # Legacy string-mode branching (matches sweep_submission.py)
            should_improve = (
                maker_mode == "improve_1"
                or (maker_mode == "improve_if_wide"
                    and spread is not None and spread >= spread_thresh)
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

        qb = int(cfg.get("quote_bias_ticks", 0))
        if qb != 0:
            bid_price += qb
            ask_price += qb

        # ── Inventory skew (around target) ───────────────────────────
        if skew_str > 0 and dev != 0:
            if is_long_dev:
                ask_price = max(math.ceil(fair) + 1, ask_price - skew_str)
                bid_price = bid_price - skew_str
            elif is_short_dev:
                bid_price = min(math.floor(fair) - 1, bid_price + skew_str)
                ask_price = ask_price + skew_str

        # ── Pressure tightening ──────────────────────────────────────
        if pressure > 0:
            if is_long_dev:
                tighter = max(math.ceil(fair) + 1, ask_price - round(pressure))
                if tighter > fair:
                    ask_price = tighter
            elif is_short_dev:
                tighter = min(math.floor(fair) - 1, bid_price + round(pressure))
                if tighter < fair:
                    bid_price = tighter

        # ── Volumes ──────────────────────────────────────────────────
        base_buy_vol = min(int(buy_cap * make_portion * bid_frac / 0.5), buy_cap)
        base_sell_vol = min(int(sell_cap * make_portion * ask_frac / 0.5), sell_cap)

        if pressure > 0:
            if is_long_dev:
                base_buy_vol = int(base_buy_vol * max(0.0, 1.0 - pressure * size_hc))
            elif is_short_dev:
                base_sell_vol = int(base_sell_vol * max(0.0, 1.0 - pressure * size_hc))

        if base_buy_vol > 0:
            orders.append(Order(product, bid_price, base_buy_vol))
        if base_sell_vol > 0:
            orders.append(Order(product, ask_price, -base_sell_vol))

        return orders, buy_cap, sell_cap, pressure, bid_price, ask_price, base_buy_vol, base_sell_vol

    # ==================================================================
    # Entry point
    # ==================================================================

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        saved = self._load_state(state.traderData)

        if not saved.get("_cfg_logged"):
            parts = [f"[CONFIG] active={ACTIVE}"]
            if ACO_ACTIVE:
                parts.append(f"aco={json.dumps({k:v for k,v in ACO_CFG.items() if v is not None})}")
            if IPR_ACTIVE:
                parts.append(f"ipr={json.dumps({k:v for k,v in IPR_CFG.items() if v is not None})}")
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
        return result, 0, traderData

    # ------------------------------------------------------------------
    # Per-product runners (silent unless EXPL_VERBOSE=1)
    # ------------------------------------------------------------------

    def _run_aco(self, product, depth, position, state, saved, result):
        aco_state = saved.setdefault("aco", {})
        cfg = ACO_CFG

        prev_ema = aco_state.get("ema")
        book_fair = self._aco_book_fair(depth)
        fair = self._aco_update_fair(book_fair, prev_ema, cfg)
        aco_state["ema"] = fair

        buy_cap = cfg["position_limit"] - position
        sell_cap = cfg["position_limit"] + position
        all_orders: List[Order] = []

        take_orders, buy_cap, sell_cap = self._phase_take_positive(
            product, depth, fair, buy_cap, sell_cap, cfg["min_take_edge"],
        )
        all_orders.extend(take_orders)

        flat_orders, buy_cap, sell_cap = self._phase_take_flatten(
            product, depth, fair, position, buy_cap, sell_cap,
            position_target=0,
        )
        all_orders.extend(flat_orders)

        mk_orders, buy_cap, sell_cap, pressure, mk_bid, mk_ask, mk_bvol, mk_svol = (
            self._phase_make(product, depth, fair, position, buy_cap, sell_cap, cfg)
        )
        all_orders.extend(mk_orders)

        result[product] = all_orders
        _vprint(f"[ACO] t={state.timestamp} pos={position:+d} fair={fair:.1f} "
                f"mk_bid={mk_bvol}@{mk_bid} mk_ask={mk_svol}@{mk_ask} pressure={pressure:.2f}")

    def _run_ipr(self, product, depth, position, state, saved, result):
        ipr_state = saved.setdefault("ipr", {})
        cfg = IPR_CFG

        prev_fair = ipr_state.get("fair")
        prev_ts = ipr_state.get("ts")
        fair = self._ipr_compute_fair(state.timestamp, depth, prev_fair, prev_ts, cfg)
        ipr_state["fair"] = fair
        ipr_state["ts"] = state.timestamp

        buy_cap = cfg["position_limit"] - position
        sell_cap = cfg["position_limit"] + position
        all_orders: List[Order] = []

        take_orders, buy_cap, sell_cap = self._phase_take_positive(
            product, depth, fair, buy_cap, sell_cap, cfg["min_take_edge"],
            long_take_edge=cfg.get("long_take_edge"),
        )
        all_orders.extend(take_orders)

        pos_target = int(cfg.get("position_target", 0))
        flat_orders, buy_cap, sell_cap = self._phase_take_flatten(
            product, depth, fair, position, buy_cap, sell_cap,
            position_target=pos_target,
        )
        all_orders.extend(flat_orders)

        mk_orders, buy_cap, sell_cap, pressure, mk_bid, mk_ask, mk_bvol, mk_svol = (
            self._phase_make(product, depth, fair, position, buy_cap, sell_cap, cfg)
        )
        all_orders.extend(mk_orders)

        result[product] = all_orders
        _vprint(f"[IPR] t={state.timestamp} pos={position:+d} fair={fair:.1f} "
                f"mk_bid={mk_bvol}@{mk_bid} mk_ask={mk_svol}@{mk_ask} pressure={pressure:.2f}")

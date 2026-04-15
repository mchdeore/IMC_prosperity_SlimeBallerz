"""
Prosperity 4 — Checkpoint Combined Strategy

Two independent sub-strategies under one Trader class:

  ASH_COATED_OSMIUM  — EMA-AnchoredMM-v2
    4-phase market maker with EMA-stabilized fair value anchored at 10,000.
    L2+L3 book fair → EMA → clamp.  Falls back to EMA on one-sided books.

  INTARIAN_PEPPER_ROOT — SkewedDelta-PepperRoot-v7
    Open-loop delta-trend fair value with deliberate long bias (70/30 skew).
    Bootstraps from book mid, then drifts at SLOPE per tick.

Both share the same 4-phase structure:
  1. Take positive-EV
  2. Take zero-EV (flatten)
  3. Make (beat book by 1 tick)
  4. Soft-cap pressure

State for each product is persisted independently via traderData (JSON).
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import math
import json

# ── ASH_COATED_OSMIUM tunables ────────────────────────────────────────
ACO = "ASH_COATED_OSMIUM"
ACO_POSITION_LIMIT = 80
ACO_SOFT_CAP = 60
ACO_MAKE_PORTION = 0.8
ACO_ANCHOR = 10_000
ACO_FAIR_CLAMP_BAND = 20
ACO_EMA_ALPHA = 0.25
ACO_MIN_TAKE_EDGE = 1

# ── INTARIAN_PEPPER_ROOT tunables ─────────────────────────────────────
IPR = "INTARIAN_PEPPER_ROOT"
IPR_POSITION_LIMIT = 80
IPR_SOFT_CAP = 75
IPR_MAKE_PORTION = 0.9
IPR_MIN_TAKE_EDGE = 1
IPR_SLOPE = 0.001
IPR_BID_FRAC = 0.70
IPR_ASK_FRAC = 0.30

PRODUCTS = {ACO, IPR}


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
    def _aco_update_fair(book_fair: Optional[float], prev_ema: Optional[float]) -> float:
        if book_fair is not None:
            clamped = max(ACO_ANCHOR - ACO_FAIR_CLAMP_BAND,
                         min(ACO_ANCHOR + ACO_FAIR_CLAMP_BAND, book_fair))
            if prev_ema is not None:
                fair = ACO_EMA_ALPHA * clamped + (1 - ACO_EMA_ALPHA) * prev_ema
            else:
                fair = clamped
        elif prev_ema is not None:
            fair = prev_ema
        else:
            fair = float(ACO_ANCHOR)

        return max(ACO_ANCHOR - ACO_FAIR_CLAMP_BAND,
                   min(ACO_ANCHOR + ACO_FAIR_CLAMP_BAND, fair))

    # ==================================================================
    # INTARIAN_PEPPER_ROOT — fair value
    # ==================================================================

    @staticmethod
    def _ipr_compute_fair(
        timestamp: int, depth: OrderDepth,
        prev_fair: Optional[float], prev_ts: Optional[int],
    ) -> float:
        if prev_fair is not None and prev_ts is not None:
            dt = timestamp - prev_ts
            return prev_fair + dt * IPR_SLOPE

        if depth.buy_orders and depth.sell_orders:
            book_mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
            return book_mid

        if depth.sell_orders:
            return min(depth.sell_orders) - 5.0
        if depth.buy_orders:
            return max(depth.buy_orders) + 5.0

        return 10000.0

    # ==================================================================
    # Shared phases (parameterised by product config)
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

    # ------------------------------------------------------------------
    # Phase 3 — make (ACO variant: symmetric)
    # ------------------------------------------------------------------

    @staticmethod
    def _phase_make_aco(
        depth: OrderDepth, fair: float, position: int,
        buy_cap: int, sell_cap: int,
    ) -> tuple:
        orders: List[Order] = []

        abs_pos = abs(position)
        if abs_pos > ACO_SOFT_CAP:
            pressure = min((abs_pos - ACO_SOFT_CAP) / (ACO_POSITION_LIMIT - ACO_SOFT_CAP), 1.0)
        else:
            pressure = 0.0

        is_long = position > 0
        is_short = position < 0

        bid_price = math.floor(fair) - 1
        for bp in sorted(depth.buy_orders, reverse=True):
            candidate = bp + 1
            if candidate < fair:
                bid_price = candidate
                break
            if bp < fair:
                bid_price = bp
                break

        ask_price = math.ceil(fair) + 1
        for ap in sorted(depth.sell_orders):
            candidate = ap - 1
            if candidate > fair:
                ask_price = candidate
                break
            if ap > fair:
                ask_price = ap
                break

        if pressure > 0:
            if is_long:
                tighter_ask = max(math.ceil(fair) + 1, ask_price - round(pressure))
                if tighter_ask > fair:
                    ask_price = tighter_ask
            elif is_short:
                tighter_bid = min(math.floor(fair) - 1, bid_price + round(pressure))
                if tighter_bid < fair:
                    bid_price = tighter_bid

        base_buy_vol = int(buy_cap * ACO_MAKE_PORTION)
        base_sell_vol = int(sell_cap * ACO_MAKE_PORTION)

        if pressure > 0:
            if is_long:
                base_buy_vol = int(base_buy_vol * (1 - pressure))
            elif is_short:
                base_sell_vol = int(base_sell_vol * (1 - pressure))

        if base_buy_vol > 0:
            orders.append(Order(ACO, bid_price, base_buy_vol))
        if base_sell_vol > 0:
            orders.append(Order(ACO, ask_price, -base_sell_vol))

        return orders, buy_cap, sell_cap, pressure, bid_price, ask_price, base_buy_vol, base_sell_vol

    # ------------------------------------------------------------------
    # Phase 3 — make (IPR variant: skewed volume)
    # ------------------------------------------------------------------

    @staticmethod
    def _phase_make_ipr(
        depth: OrderDepth, fair: float, position: int,
        buy_cap: int, sell_cap: int,
    ) -> tuple:
        orders: List[Order] = []

        is_long = position > 0
        is_short = position < 0

        if is_short and abs(position) > IPR_SOFT_CAP:
            pressure = min((abs(position) - IPR_SOFT_CAP) / (IPR_POSITION_LIMIT - IPR_SOFT_CAP), 1.0)
        elif is_long and position > IPR_POSITION_LIMIT - 2:
            pressure = 0.5
        else:
            pressure = 0.0

        bid_price = math.floor(fair) - 1
        for bp in sorted(depth.buy_orders, reverse=True):
            candidate = bp + 1
            if candidate < fair:
                bid_price = candidate
                break
            if bp < fair:
                bid_price = bp
                break

        ask_price = math.ceil(fair) + 1
        for ap in sorted(depth.sell_orders):
            candidate = ap - 1
            if candidate > fair:
                ask_price = candidate
                break
            if ap > fair:
                ask_price = ap
                break

        if pressure > 0:
            if is_long:
                tighter_ask = max(math.ceil(fair) + 1, ask_price - round(pressure))
                if tighter_ask > fair:
                    ask_price = tighter_ask
            elif is_short:
                tighter_bid = min(math.floor(fair) - 1, bid_price + round(pressure))
                if tighter_bid < fair:
                    bid_price = tighter_bid

        base_buy_vol = min(int(buy_cap * IPR_MAKE_PORTION * IPR_BID_FRAC / 0.5), buy_cap)
        base_sell_vol = min(int(sell_cap * IPR_MAKE_PORTION * IPR_ASK_FRAC / 0.5), sell_cap)

        if pressure > 0:
            if is_long:
                base_buy_vol = int(base_buy_vol * (1 - pressure))
            elif is_short:
                base_sell_vol = int(base_sell_vol * (1 - pressure))

        if base_buy_vol > 0:
            orders.append(Order(IPR, bid_price, base_buy_vol))
        if base_sell_vol > 0:
            orders.append(Order(IPR, ask_price, -base_sell_vol))

        return orders, buy_cap, sell_cap, pressure, bid_price, ask_price, base_buy_vol, base_sell_vol

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        saved = self._load_state(state.traderData)

        for product in state.order_depths:
            depth = state.order_depths[product]
            position = state.position.get(product, 0)

            if product == ACO:
                self._run_aco(product, depth, position, state, saved, result)
            elif product == IPR:
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

        prev_ema = aco_state.get("ema")
        book_fair = self._aco_book_fair(depth)
        fair = self._aco_update_fair(book_fair, prev_ema)
        aco_state["ema"] = fair

        both_sides = bool(depth.buy_orders and depth.sell_orders)

        buy_cap_init = ACO_POSITION_LIMIT - position
        sell_cap_init = ACO_POSITION_LIMIT + position
        buy_cap = buy_cap_init
        sell_cap = sell_cap_init

        all_orders: List[Order] = []

        take_orders, buy_cap, sell_cap = self._phase_take_positive(
            product, depth, fair, buy_cap, sell_cap, ACO_MIN_TAKE_EDGE,
        )
        all_orders.extend(take_orders)

        flat_orders, buy_cap, sell_cap = self._phase_take_flatten(
            product, depth, fair, position, buy_cap, sell_cap,
        )
        all_orders.extend(flat_orders)

        make_orders, buy_cap, sell_cap, pressure, mk_bid, mk_ask, mk_bvol, mk_svol = self._phase_make_aco(
            depth, fair, position, buy_cap, sell_cap,
        )
        all_orders.extend(make_orders)

        result[product] = all_orders

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
        src = "L23" if book_fair is not None else "EMA"

        print(
            f"[ACO-EMA-MM] t={state.timestamp} | pos={position:+d} | fair={fair:.1f}({src}) "
            f"| spread={spread} | cap B/S={buy_cap_init}/{sell_cap_init}"
        )
        print(f"  book: {self._log_book(depth)}")
        if not both_sides:
            print(f"  *** ONE-SIDED BOOK — using EMA fair ***")
        print(self._log_orders("TAKE", take_orders))
        print(self._log_orders("FLAT", flat_orders))
        if pressure > 0:
            print(
                f"  MAKE (pressure={pressure:.2f}): "
                f"bid {mk_bvol}@{mk_bid}, ask {mk_svol}@{mk_ask}"
            )
        else:
            print(self._log_orders("MAKE", make_orders))

    def _run_ipr(self, product, depth, position, state, saved, result):
        ipr_state = saved.setdefault("ipr", {})

        prev_fair = ipr_state.get("fair")
        prev_ts = ipr_state.get("ts")
        fair = self._ipr_compute_fair(state.timestamp, depth, prev_fair, prev_ts)
        ipr_state["fair"] = fair
        ipr_state["ts"] = state.timestamp

        buy_cap_init = IPR_POSITION_LIMIT - position
        sell_cap_init = IPR_POSITION_LIMIT + position
        buy_cap = buy_cap_init
        sell_cap = sell_cap_init

        all_orders: List[Order] = []

        take_orders, buy_cap, sell_cap = self._phase_take_positive(
            product, depth, fair, buy_cap, sell_cap, IPR_MIN_TAKE_EDGE,
        )
        all_orders.extend(take_orders)

        flat_orders, buy_cap, sell_cap = self._phase_take_flatten(
            product, depth, fair, position, buy_cap, sell_cap,
        )
        all_orders.extend(flat_orders)

        make_orders, buy_cap, sell_cap, pressure, mk_bid, mk_ask, mk_bvol, mk_svol = self._phase_make_ipr(
            depth, fair, position, buy_cap, sell_cap,
        )
        all_orders.extend(make_orders)

        result[product] = all_orders

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
        src = "BOOT" if prev_fair is None else "DELTA"

        print(
            f"[IPR-SkewDelta] t={state.timestamp} | pos={position:+d} | fair={fair:.1f}({src}) "
            f"| spread={spread} | cap B/S={buy_cap_init}/{sell_cap_init}"
        )
        print(f"  book: {self._log_book(depth)}")
        print(self._log_orders("TAKE", take_orders))
        print(self._log_orders("FLAT", flat_orders))
        if pressure > 0:
            print(
                f"  MAKE (pressure={pressure:.2f}): "
                f"bid {mk_bvol}@{mk_bid}, ask {mk_svol}@{mk_ask}"
            )
        else:
            print(self._log_orders("MAKE", make_orders))


# ======================================================================
# Local smoke test
# ======================================================================
if __name__ == "__main__":
    from datamodel import Listing, Observation

    def make_state(products_data, timestamp=100, td=""):
        """
        products_data: dict of product -> (buy_orders, sell_orders, position)
        """
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

    t = Trader()

    print("=" * 60)
    print("COMBINED SMOKE TEST")
    print("=" * 60)

    print("\n=== Tick 1: Both products, flat positions ===")
    s = make_state({
        ACO: ({9994: 15, 9991: 21}, {10010: -15, 10013: -21}, 0),
        IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, 0),
    }, timestamp=0)
    r, _, td = t.run(s)
    for prod in [ACO, IPR]:
        print(f"  {prod} orders: {r[prod]}")

    print("\n=== Tick 2: ACO one-sided, IPR normal ===")
    s = make_state({
        ACO: ({}, {10009: -15, 10011: -21}, 0),
        IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, 10),
    }, timestamp=100, td=td)
    r, _, td = t.run(s)
    for prod in [ACO, IPR]:
        print(f"  {prod} orders: {r[prod]}")

    print("\n=== Tick 3: Both with positions ===")
    s = make_state({
        ACO: ({9993: 14, 9991: 25}, {10009: -14, 10012: -25}, 30),
        IPR: ({11992: 17, 11989: 21}, {12006: -11, 12009: -21}, -20),
    }, timestamp=200, td=td)
    r, _, td = t.run(s)
    for prod in [ACO, IPR]:
        print(f"  {prod} orders: {r[prod]}")

    print("\n=== State check ===")
    state = json.loads(td)
    print(f"  ACO EMA: {state['aco']['ema']:.1f}")
    print(f"  IPR fair: {state['ipr']['fair']:.1f}, ts: {state['ipr']['ts']}")
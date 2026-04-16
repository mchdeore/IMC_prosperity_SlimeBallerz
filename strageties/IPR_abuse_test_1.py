"""
Prosperity 4 — IPR_abuse_test_1 (INTARIAN_PEPPER_ROOT only)
===========================================================
Isolated experiment: exploit the empirical **linear upward drift** of IPR mid
(~0.001 price units per ms of exchange time on bundled round-1 CSVs → ~+1000
over a ~1e6 ms day). Strategy: **build max long early**, **stay flat on the
book while at limit** (no sells = no spread paid on the exit side until the end),
then **aggressively liquidate in a short tail window** so inventory clears before
the day ends.

Round-trip spread dominates frequent cycling, so the default is **one buy–hold–dump
cycle per day** (repeat naturally on the next simulation day). Tunables below
were chosen from the observed slope band (~0.001–0.00103 / ms) and a very late
liquidation start (~99.99% through the day) to preserve drift exposure while
leaving only the last ~100 ms of the session to flatten (tuned vs round-1 `prosperity4btest`).

Only **INTARIAN_PEPPER_ROOT** is traded (no ASH_COATED_OSMIUM orders).
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import json
import math

STRATEGY_NAME = "IPR_abuse_test_1"

IPR = "INTARIAN_PEPPER_ROOT"

# Empirical drift from ROUND_1_DATA price CSVs (linear fit on mid_price vs timestamp).
SLOPE_PER_MS = 0.001

# Position cap (Prosperity default for this product in repo strategies).
POSITION_LIMIT = 80

# Exchange timeline: bundled days use 0 … 999_900 in 100 ms steps (~1e6 ms days).
DAY_MS = 1_000_000

# Start dumping at this fraction of the day. On bundled round-1 backtests, merged
# PnL rose monotonically with later starts up to ~0.9999 (≈ timestamp 999_900 on
# 1e6 ms days): hold almost the full drift, then clear in the final ticks.
LIQUIDATION_START_FRAC = 0.9999
LIQUIDATION_START_MS = int(DAY_MS * LIQUIDATION_START_FRAC)

# Taking liquidity: same convention as sweep/optimized (buy asks cheaper than this).
MIN_TAKE_EDGE = 1

# Maker bid aggression while still accumulating (improve one tick inside when wide).
MAKER_MODE_IMPROVE = True


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

    @staticmethod
    def _ipr_fair(
        timestamp: int,
        depth: OrderDepth,
        prev_fair: Optional[float],
        prev_ts: Optional[int],
    ) -> float:
        if prev_fair is not None and prev_ts is not None:
            return prev_fair + (timestamp - prev_ts) * SLOPE_PER_MS

        if depth.buy_orders and depth.sell_orders:
            return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
        if depth.sell_orders:
            return float(min(depth.sell_orders)) - 5.0
        if depth.buy_orders:
            return float(max(depth.buy_orders)) + 5.0
        return 10_000.0

    @staticmethod
    def _phase_buy_edge(
        product: str,
        depth: OrderDepth,
        fair: float,
        buy_cap: int,
        min_edge: int,
    ) -> List[Order]:
        """Lift asks with positive edge vs fair (same logic as optimized take_positive buys)."""
        orders: List[Order] = []
        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair - min_edge or buy_cap <= 0:
                break
            qty = min(-depth.sell_orders[ask_px], buy_cap)
            if qty > 0:
                orders.append(Order(product, ask_px, qty))
                buy_cap -= qty
        return orders

    @staticmethod
    def _phase_maker_bid_only(
        product: str,
        depth: OrderDepth,
        fair: float,
        buy_cap: int,
    ) -> List[Order]:
        """Single bid to top up toward the limit; no ask (no inventory-reducing sells)."""
        if buy_cap <= 0 or not depth.buy_orders:
            return []

        bid_price = math.floor(fair) - 1
        for bp in sorted(depth.buy_orders, reverse=True):
            if MAKER_MODE_IMPROVE:
                cand = bp + 1
                if cand < fair:
                    bid_price = cand
                    break
            if bp < fair:
                bid_price = bp
                break

        return [Order(product, bid_price, buy_cap)]

    @staticmethod
    def _dump_into_bids(product: str, depth: OrderDepth, sell_cap: int) -> List[Order]:
        """Market-style sell: walk bids from best downward."""
        orders: List[Order] = []
        for bid_px in sorted(depth.buy_orders, reverse=True):
            if sell_cap <= 0:
                break
            avail = depth.buy_orders[bid_px]
            qty = min(avail, sell_cap)
            if qty > 0:
                orders.append(Order(product, bid_px, -qty))
                sell_cap -= qty
        return orders

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        saved = self._load_state(state.traderData)
        ipr_state = saved.setdefault("ipr", {})

        if not saved.get("_logged"):
            print(
                f"[{STRATEGY_NAME}] IPR-only | slope/ms={SLOPE_PER_MS} "
                f"liq_start>={LIQUIDATION_START_MS} (~{LIQUIDATION_START_FRAC:.0%} day) "
                f"pos_limit={POSITION_LIMIT}"
            )
            saved["_logged"] = True

        for product in state.order_depths:
            if product != IPR:
                result[product] = []
                continue

            depth = state.order_depths[product]
            position = state.position.get(product, 0)

            prev_fair = ipr_state.get("fair")
            prev_ts = ipr_state.get("ts")
            fair = self._ipr_fair(state.timestamp, depth, prev_fair, prev_ts)
            ipr_state["fair"] = fair
            ipr_state["ts"] = state.timestamp

            buy_cap = POSITION_LIMIT - position
            sell_cap = POSITION_LIMIT + position

            # Safety: if short, cover with buys before anything else.
            if position < 0:
                cover = min(-position, buy_cap)
                orders_cover = self._phase_buy_edge(
                    product, depth, fair, cover, MIN_TAKE_EDGE,
                )
                result[product] = orders_cover
                continue

            t = state.timestamp

            # Late session: exit only (no new long adds) — maximizes drift then realizes.
            if t >= LIQUIDATION_START_MS:
                to_sell = min(position, sell_cap)
                if to_sell <= 0:
                    result[product] = []
                else:
                    result[product] = self._dump_into_bids(product, depth, to_sell)
                continue

            # Pre-liquidation: reach limit, then wait (no sells).
            if position >= POSITION_LIMIT:
                result[product] = []
                continue

            # Accumulate: edge buys first, then one large maker bid for remainder.
            orders: List[Order] = []
            edge_orders = self._phase_buy_edge(
                product, depth, fair, buy_cap, MIN_TAKE_EDGE,
            )
            orders.extend(edge_orders)
            used = sum(o.quantity for o in edge_orders)
            rem = buy_cap - used
            if rem > 0:
                orders.extend(
                    self._phase_maker_bid_only(product, depth, fair, rem),
                )
            result[product] = orders

        return result, 0, self._save_state(saved)


if __name__ == "__main__":
    from datamodel import Listing, Observation

    od = OrderDepth()
    od.buy_orders = {11990: 20}
    od.sell_orders = {12005: -15}
    st = TradingState(
        traderData="",
        timestamp=100,
        listings={IPR: Listing(symbol=IPR, product=IPR, denomination="XIRECS")},
        order_depths={IPR: od},
        own_trades={IPR: []},
        market_trades={IPR: []},
        position={IPR: 0},
        observations=Observation({}, {}),
    )
    tr = Trader()
    r, _, td = tr.run(st)
    print(STRATEGY_NAME, "smoke:", r, "td_len", len(td))

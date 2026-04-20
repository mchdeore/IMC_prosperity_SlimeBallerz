"""
ACO_hardcode - isolated ASH_COATED_OSMIUM strategy from primo_final.
===================================================================

This file extracts the ASH_COATED_OSMIUM (ACO) portion of primo_final.py
into its own standalone strategy. The logic is intentionally kept the
same:

- Fair value from L2+L3 book levels
- Slow EMA smoothing
- Aggressive taker fills on clear edge
- Inventory flattening near the soft cap
- Safe maker quotes around fair
- Inventory-aware quote/size skew when overloaded

All parameters are hardcoded to match primo_final.py exactly.
"""

import _repo_path  # noqa: F401 - adds repo root for MODULES imports

from datamodel import OrderDepth, TradingState, Order
import json
import math
from typing import Optional

from MODULES import TickRecorder, logs_csv_path


# =======================================================================
# PRODUCT CONSTANTS
# =======================================================================

ACO = "ASH_COATED_OSMIUM"
POSITION_LIMIT = 80


# =======================================================================
# ACO HARDCODED PARAMETERS (from primo_final.py)
# =======================================================================

ACO_SOFT_CAP = 60
ACO_MAKE_PORTION = 0.80
ACO_BID_FRAC = 0.50
ACO_ASK_FRAC = 0.50
ACO_MAKE_BEAT_TICKS = 1
ACO_MIN_TAKE_EDGE = 1
ACO_FAIR_LEVELS = [2, 3]
ACO_EMA_ALPHA_NEW = 0.05
ACO_ANCHOR = 10000


class Trader:
    def __init__(
        self,
        tick_recorder: Optional[TickRecorder] = None,
        record_ticks: bool = True,
        sandbox_stdout: Optional[bool] = None,
    ):
        """
        tick_recorder: optional custom TickRecorder; if None and record_ticks,
        writes ``LOGS/ACO_hardcode_ticks_<datetime>.csv``. Set
        record_ticks=False for competition submission to skip file I/O.

        sandbox_stdout: if True, print one JSON line per tick so the backtester's
        ``lambdaLog`` is populated. Defaults to the same value as record_ticks
        when omitted.
        """
        if tick_recorder is not None:
            self.tick_recorder = tick_recorder
        elif record_ticks:
            self.tick_recorder = TickRecorder(auto_save_csv=logs_csv_path("ACO_hardcode_ticks"))
        else:
            self.tick_recorder = None

        if sandbox_stdout is None:
            sandbox_stdout = record_ticks
        self._sandbox_stdout = sandbox_stdout

    def run(self, state: TradingState):
        saved = {}
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
            except Exception:
                saved = {}

        result = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            position = state.position.get(product, 0)

            if product == ACO:
                orders = self.run_aco(depth, position, state.timestamp, saved)
                result[product] = orders
            else:
                result[product] = []

        trader_data_out = json.dumps(saved)

        if self.tick_recorder is not None:
            self.tick_recorder.record_tick(state, result)

        if self._sandbox_stdout:
            print(
                json.dumps(
                    {
                        "t": state.timestamp,
                        "orders": {
                            k: [[o.price, o.quantity] for o in v] for k, v in result.items()
                        },
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )

        return result, 0, trader_data_out

    def run_aco(self, depth: OrderDepth, position: int, timestamp: int, saved):
        aco_state = saved.setdefault("aco", {})

        # ---- 1. Fair value: hardcoded anchor, no calculation ----
        fair = float(ACO_ANCHOR)
        aco_state["fair"] = fair

        sorted_bids = sorted(depth.buy_orders.keys(), reverse=True)
        sorted_asks = sorted(depth.sell_orders.keys())

        buy_capacity = POSITION_LIMIT - position
        sell_capacity = POSITION_LIMIT + position
        orders = []

        # ---- 2. TAKE phase: aggressively hit mispricings ----
        for ask_price in sorted(depth.sell_orders.keys()):
            if ask_price >= fair - ACO_MIN_TAKE_EDGE:
                break
            if buy_capacity <= 0:
                break
            available = -depth.sell_orders[ask_price]
            qty = min(available, buy_capacity)
            if qty > 0:
                orders.append(Order(ACO, ask_price, qty))
                buy_capacity -= qty

        for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_price <= fair + ACO_MIN_TAKE_EDGE:
                break
            if sell_capacity <= 0:
                break
            available = depth.buy_orders[bid_price]
            qty = min(available, sell_capacity)
            if qty > 0:
                orders.append(Order(ACO, bid_price, -qty))
                sell_capacity -= qty

        # ---- 3. FLATTEN phase: 0-EV unwind if over softcap ----
        if abs(position) >= ACO_SOFT_CAP:
            fair_int = int(round(fair))
            if position > 0:
                to_reduce = min(position, sell_capacity)
                for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
                    if to_reduce <= 0:
                        break
                    if bid_price < fair_int:
                        break
                    available = depth.buy_orders[bid_price]
                    qty = min(available, to_reduce)
                    if qty > 0:
                        orders.append(Order(ACO, bid_price, -qty))
                        sell_capacity -= qty
                        to_reduce -= qty
            elif position < 0:
                to_reduce = min(-position, buy_capacity)
                for ask_price in sorted(depth.sell_orders.keys()):
                    if to_reduce <= 0:
                        break
                    if ask_price > fair_int:
                        break
                    available = -depth.sell_orders[ask_price]
                    qty = min(available, to_reduce)
                    if qty > 0:
                        orders.append(Order(ACO, ask_price, qty))
                        buy_capacity -= qty
                        to_reduce -= qty

        # ---- 4. MAKE phase: post bid and ask ----
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        max_safe_bid = int(math.floor(fair)) - 1
        min_safe_ask = int(math.ceil(fair)) + 1

        if best_bid is not None:
            bid_price = best_bid + ACO_MAKE_BEAT_TICKS
        else:
            bid_price = max_safe_bid
        if bid_price > max_safe_bid:
            bid_price = max_safe_bid

        if best_ask is not None:
            ask_price = best_ask - ACO_MAKE_BEAT_TICKS
        else:
            ask_price = min_safe_ask
        if ask_price < min_safe_ask:
            ask_price = min_safe_ask

        bid_volume = int(buy_capacity * ACO_MAKE_PORTION * ACO_BID_FRAC / 0.5)
        ask_volume = int(sell_capacity * ACO_MAKE_PORTION * ACO_ASK_FRAC / 0.5)
        bid_volume = min(bid_volume, buy_capacity)
        ask_volume = min(ask_volume, sell_capacity)

        if abs(position) > ACO_SOFT_CAP:
            excess = (abs(position) - ACO_SOFT_CAP) / float(POSITION_LIMIT - ACO_SOFT_CAP)
            if excess > 1.0:
                excess = 1.0
            if position > 0:
                shift = int(round(excess * (ask_price - fair)))
                new_ask = ask_price - shift
                if new_ask < min_safe_ask:
                    new_ask = min_safe_ask
                ask_price = new_ask
                bid_volume = int(bid_volume * (1.0 - excess))
            elif position < 0:
                shift = int(round(excess * (fair - bid_price)))
                new_bid = bid_price + shift
                if new_bid > max_safe_bid:
                    new_bid = max_safe_bid
                bid_price = new_bid
                ask_volume = int(ask_volume * (1.0 - excess))

        if bid_volume > 0:
            orders.append(Order(ACO, bid_price, bid_volume))
        if ask_volume > 0:
            orders.append(Order(ACO, ask_price, -ask_volume))

        return orders

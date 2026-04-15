"""
osmium_edge_test_1 — Competition Submission
============================================
Market-making strategy for ASH_COATED_OSMIUM with edge=7 from wall mid.

Self-contained single file. No external dependencies beyond datamodel.
Submit THIS file to the IMC Prosperity platform.

Strategy:
  - Fair price = Wall Mid (deepest-liquidity bid/ask average), clamped
    near 10,000 anchor, smoothed with EMA
  - Take any orders that cross wall_mid +/- 7
  - Make passive quotes at wall_mid +/- 7
  - Flatten inventory at fair when |position| >= 40
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import math
import json

# ── Parameters ────────────────────────────────────────────────────────────────

PRODUCT = "ASH_COATED_OSMIUM"
POSITION_LIMIT = 80
EDGE = 7
FLATTEN_THRESHOLD = 40
ANCHOR = 10_000
CLAMP_BAND = 25
EMA_ALPHA = 0.25


class Trader:

    def _load_state(self, td: str) -> dict:
        if not td:
            return {}
        try:
            return json.loads(td)
        except Exception:
            return {}

    def _save_state(self, state: dict) -> str:
        return json.dumps(state)

    def _wall_mid(self, depth: OrderDepth) -> Optional[float]:
        """
        Compute wall mid: average of the deepest-volume bid and ask levels.
        Returns None if either side is empty.
        """
        if not depth.buy_orders or not depth.sell_orders:
            return None

        best_bid = max(depth.buy_orders.keys())
        best_ask = min(depth.sell_orders.keys())

        bid_wall_price = best_bid
        bid_wall_vol = 0
        for price, vol in depth.buy_orders.items():
            if abs(vol) > bid_wall_vol:
                bid_wall_vol = abs(vol)
                bid_wall_price = price

        ask_wall_price = best_ask
        ask_wall_vol = 0
        for price, vol in depth.sell_orders.items():
            if abs(vol) > ask_wall_vol:
                ask_wall_vol = abs(vol)
                ask_wall_price = price

        mid = (bid_wall_price + ask_wall_price) / 2.0

        # Clamp to BBO so wall_mid never falls outside the spread
        mid = max(best_bid, min(best_ask, mid))

        return mid

    def _update_fair(self, wall_mid: Optional[float], prev_ema: Optional[float]) -> float:
        """EMA-smoothed fair value clamped near the anchor."""
        if wall_mid is not None:
            clamped = max(ANCHOR - CLAMP_BAND, min(ANCHOR + CLAMP_BAND, wall_mid))
            if prev_ema is not None:
                fair = EMA_ALPHA * clamped + (1 - EMA_ALPHA) * prev_ema
            else:
                fair = clamped
        elif prev_ema is not None:
            fair = prev_ema
        else:
            fair = float(ANCHOR)

        return max(ANCHOR - CLAMP_BAND, min(ANCHOR + CLAMP_BAND, fair))

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        saved = self._load_state(state.traderData)

        for product in state.order_depths:
            if product != PRODUCT:
                result[product] = []
                continue

            depth = state.order_depths[product]
            position = state.position.get(product, 0)
            orders: List[Order] = []

            prev_ema = saved.get("ema")
            wm = self._wall_mid(depth)
            fair = self._update_fair(wm, prev_ema)
            saved["ema"] = fair

            buy_cap = POSITION_LIMIT - position
            sell_cap = POSITION_LIMIT + position

            bid_target = math.floor(fair - EDGE)
            ask_target = math.ceil(fair + EDGE)

            # Phase 1: Take any orders that cross our edge threshold
            for ask_px in sorted(depth.sell_orders):
                if ask_px > bid_target or buy_cap <= 0:
                    break
                qty = min(-depth.sell_orders[ask_px], buy_cap)
                if qty > 0:
                    orders.append(Order(product, ask_px, qty))
                    buy_cap -= qty

            for bid_px in sorted(depth.buy_orders, reverse=True):
                if bid_px < ask_target or sell_cap <= 0:
                    break
                qty = min(depth.buy_orders[bid_px], sell_cap)
                if qty > 0:
                    orders.append(Order(product, bid_px, -qty))
                    sell_cap -= qty

            # Phase 2: Flatten toward zero if inventory is too large
            position_after = position + sum(o.quantity for o in orders)
            fair_int = round(fair)

            if position_after > FLATTEN_THRESHOLD:
                flatten_vol = min(position_after - FLATTEN_THRESHOLD // 2, sell_cap)
                for bid_px in sorted(depth.buy_orders, reverse=True):
                    if bid_px < fair_int or flatten_vol <= 0:
                        break
                    qty = min(depth.buy_orders[bid_px], flatten_vol, sell_cap)
                    if qty > 0:
                        orders.append(Order(product, bid_px, -qty))
                        sell_cap -= qty
                        flatten_vol -= qty

            elif position_after < -FLATTEN_THRESHOLD:
                flatten_vol = min(abs(position_after) - FLATTEN_THRESHOLD // 2, buy_cap)
                for ask_px in sorted(depth.sell_orders):
                    if ask_px > fair_int or flatten_vol <= 0:
                        break
                    qty = min(-depth.sell_orders[ask_px], flatten_vol, buy_cap)
                    if qty > 0:
                        orders.append(Order(product, ask_px, qty))
                        buy_cap -= qty
                        flatten_vol -= qty

            # Phase 3: Post passive quotes at wall_mid +/- EDGE
            position_after = position + sum(o.quantity for o in orders)
            buy_cap = POSITION_LIMIT - position_after
            sell_cap = POSITION_LIMIT + position_after

            if buy_cap > 0:
                orders.append(Order(product, bid_target, buy_cap))
            if sell_cap > 0:
                orders.append(Order(product, ask_target, -sell_cap))

            result[product] = orders

        trader_data = self._save_state(saved)
        conversions = 0
        return result, conversions, trader_data

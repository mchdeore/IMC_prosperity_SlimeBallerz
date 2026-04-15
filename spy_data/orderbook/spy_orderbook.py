"""
Spy: Full Orderbook Snapshotter
================================
Captures ALL orderbook levels every tick (not just top 3 from CSVs).
Optionally probes hidden depth by placing tiny resting orders when the
visible book is thin, then flattens back toward zero.

Deploy on the Prosperity simulator and parse the log with log_parser.py.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import json

POSITION_LIMIT = 80
PROBE_QTY = 1
THIN_BOOK_THRESHOLD = 3  # probe if fewer than this many levels on a side
FLATTEN_THRESHOLD = 5    # start flattening when |pos| exceeds this
TRADER_DATA_LIMIT = 45_000  # safety margin below the 50K hard cap


def safe_order(product: str, price: int, qty: int, position: int) -> Optional[Order]:
    """Clamp qty so position stays within +/-POSITION_LIMIT."""
    if qty > 0:
        max_buy = POSITION_LIMIT - position
        qty = min(qty, max_buy)
    elif qty < 0:
        max_sell = POSITION_LIMIT + position
        qty = max(qty, -max_sell)
    if qty == 0:
        return None
    return Order(product, price, qty)


def snapshot_book(depth: OrderDepth) -> dict:
    """Extract full book as sorted lists + summary stats."""
    bids = sorted(depth.buy_orders.items(), reverse=True)
    asks = sorted(depth.sell_orders.items())

    bid_vol = sum(v for _, v in bids)
    ask_vol = sum(abs(v) for _, v in asks)

    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

    return {
        "bids": [[p, v] for p, v in bids],
        "asks": [[p, abs(v)] for p, v in asks],
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "bb": best_bid,
        "ba": best_ask,
        "spread": spread,
    }


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
        s = json.dumps(state)
        if len(s) > TRADER_DATA_LIMIT:
            print(f"WARNING: traderData {len(s)} chars, over {TRADER_DATA_LIMIT} limit")
            state = {"tick": state.get("tick", 0)}
            s = json.dumps(state)
        return s

    def run(self, state: TradingState):
        saved = self._load_state(state.traderData)
        result: Dict[str, List[Order]] = {}
        log_payload: Dict = {}

        positions: Dict[str, int] = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            position = state.position.get(product, 0)
            positions[product] = position

            book = snapshot_book(depth)
            orders: List[Order] = []
            probe_info = None

            # --- Flatten if position drifted from previous probes ---
            if abs(position) > FLATTEN_THRESHOLD:
                if position > 0 and book["bb"] is not None:
                    o = safe_order(product, book["bb"], -min(position, PROBE_QTY), position)
                    if o:
                        orders.append(o)
                elif position < 0 and book["ba"] is not None:
                    o = safe_order(product, book["ba"], min(abs(position), PROBE_QTY), position)
                    if o:
                        orders.append(o)

            # --- Probe thin sides of the book ---
            elif book["bb"] is not None and book["ba"] is not None:
                if book["bid_levels"] < THIN_BOOK_THRESHOLD:
                    probe_price = book["bids"][-1][0] - 1  # one tick below deepest visible bid
                    o = safe_order(product, probe_price, PROBE_QTY, position)
                    if o:
                        orders.append(o)
                        probe_info = {"side": "buy", "price": probe_price, "qty": PROBE_QTY}
                elif book["ask_levels"] < THIN_BOOK_THRESHOLD:
                    probe_price = book["asks"][-1][0] + 1  # one tick above deepest visible ask
                    o = safe_order(product, probe_price, -PROBE_QTY, position)
                    if o:
                        orders.append(o)
                        probe_info = {"side": "sell", "price": probe_price, "qty": PROBE_QTY}

            book["probe"] = probe_info

            # Check if previous probe filled
            prev_probe = saved.get(f"{product}_probe")
            if prev_probe and product in state.own_trades:
                fills = state.own_trades[product]
                filled = any(
                    t.price == prev_probe["price"] for t in fills
                )
                book["prev_probe_filled"] = filled
            else:
                book["prev_probe_filled"] = None

            # Save current probe for next tick
            saved[f"{product}_probe"] = probe_info

            log_payload[product] = book
            result[product] = orders

        saved["tick"] = saved.get("tick", 0) + 1

        print("SPY_BOOK|" + json.dumps({
            "t": state.timestamp,
            "tick": saved["tick"],
            "pos": positions,
            "books": log_payload,
        }))

        return result, 0, self._save_state(saved)


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
            listings=listings, order_depths=order_depths,
            own_trades={p: [] for p in products_data},
            market_trades={p: [] for p in products_data},
            position=positions,
            observations=Observation({}, {}),
        )

    t = Trader()
    print("=" * 60)
    print("SPY_ORDERBOOK SMOKE TEST")
    print("=" * 60)

    print("\n--- Tick 1: Normal book (>= 3 levels each side) ---")
    s = make_state({
        "ASH_COATED_OSMIUM": (
            {9998: 10, 9996: 20, 9994: 15},
            {10002: -10, 10004: -20, 10006: -15},
            0,
        ),
    }, timestamp=0)
    r, _, td = t.run(s)
    print(f"  Orders: {r}")

    print("\n--- Tick 2: Thin bid side (only 2 levels) -> should probe ---")
    s = make_state({
        "ASH_COATED_OSMIUM": (
            {9998: 10, 9996: 20},
            {10002: -10, 10004: -20, 10006: -15},
            0,
        ),
    }, timestamp=100, td=td)
    r, _, td = t.run(s)
    print(f"  Orders: {r}")

    print("\n--- Tick 3: Position drifted to +10 -> should flatten ---")
    s = make_state({
        "ASH_COATED_OSMIUM": (
            {9998: 10, 9996: 20, 9994: 15},
            {10002: -10, 10004: -20, 10006: -15},
            10,
        ),
    }, timestamp=200, td=td)
    r, _, td = t.run(s)
    print(f"  Orders: {r}")

    print("\n--- Position limit check: pos=79, should only buy 1 ---")
    s = make_state({
        "ASH_COATED_OSMIUM": (
            {9998: 10},
            {10002: -10, 10004: -20, 10006: -15},
            79,
        ),
    }, timestamp=300, td=td)
    r, _, td = t.run(s)
    print(f"  Orders: {r}")
    for product, orders in r.items():
        for o in orders:
            new_pos = 79 + o.quantity
            assert abs(new_pos) <= POSITION_LIMIT, f"Position limit breached: {new_pos}"
    print("  Position limit OK")
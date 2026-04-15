"""
Spy: Trade Recorder & Fill-Rate Prober
========================================
Records every market trade with full detail each tick.
Places small probe orders at varying offsets from mid to build a
fill-probability curve, answering "how aggressive must I be to get fills?"

Position is aggressively flattened when |pos| > FLATTEN_THRESHOLD to
keep risk minimal.

Deploy on the Prosperity simulator and parse the log with log_parser.py.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import json
import math

POSITION_LIMIT = 80
PROBE_QTY = 1
FLATTEN_THRESHOLD = 10
PROBE_OFFSETS = [-3, -2, -1, 0, 1, 2, 3]  # offsets from mid to cycle through
TRADER_DATA_LIMIT = 45_000  # safety margin below the 50K hard cap
FILL_HISTORY_MAX = 100  # max entries; ~60 chars each with compact keys = ~6K


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


def compute_mid(depth: OrderDepth) -> Optional[float]:
    if depth.buy_orders and depth.sell_orders:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
    return None


def serialize_trades(trades) -> list:
    """Convert a list of Trade objects to compact dicts."""
    out = []
    for t in trades:
        out.append({
            "p": t.price,
            "q": t.quantity,
            "b": t.buyer or "",
            "s": t.seller or "",
            "ts": t.timestamp,
        })
    return out


def trade_stats(trades) -> dict:
    """Compute count, total volume, and VWAP for a list of trades."""
    if not trades:
        return {"count": 0, "volume": 0, "vwap": None}
    total_vol = sum(t.quantity for t in trades)
    if total_vol == 0:
        return {"count": len(trades), "volume": 0, "vwap": None}
    vwap = sum(t.price * t.quantity for t in trades) / total_vol
    return {"count": len(trades), "volume": total_vol, "vwap": round(vwap, 2)}


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
            print(f"WARNING: traderData {len(s)} chars, trimming fill_history")
            fh = state.get("fh", [])
            while len(s) > TRADER_DATA_LIMIT and fh:
                fh.pop(0)
                state["fh"] = fh
                s = json.dumps(state)
        return s

    def run(self, state: TradingState):
        saved = self._load_state(state.traderData)
        tick = saved.get("tick", 0) + 1
        saved["tick"] = tick

        probe_idx = saved.get("probe_idx", 0)
        products = sorted(state.order_depths.keys())
        probe_product_idx = saved.get("probe_product_idx", 0)

        result: Dict[str, List[Order]] = {}
        positions: Dict[str, int] = {}
        market_log: Dict[str, list] = {}
        own_log: Dict[str, list] = {}
        stats_log: Dict[str, dict] = {}

        # --- Check if previous probe filled ---
        prev_probe = saved.get("pp")
        prev_fill = None
        if prev_probe:
            prod = prev_probe["pr"]
            if prod in state.own_trades:
                fills = state.own_trades[prod]
                prev_fill = any(t.price == prev_probe["px"] for t in fills)
            else:
                prev_fill = False

        # Record fill result in history using compact keys:
        #   t=timestamp, pr=product, o=offset, sd=side, px=price, f=filled
        fill_history = saved.get("fh", [])
        if prev_probe and prev_fill is not None:
            fill_history.append({
                "t": prev_probe.get("t"),
                "pr": prev_probe["pr"],
                "o": prev_probe["o"],
                "sd": prev_probe["sd"],
                "px": prev_probe["px"],
                "f": prev_fill,
            })
            if len(fill_history) > FILL_HISTORY_MAX:
                fill_history = fill_history[-FILL_HISTORY_MAX:]
        saved["fh"] = fill_history

        probe_info = None

        for product in products:
            depth = state.order_depths[product]
            position = state.position.get(product, 0)
            positions[product] = position

            # Log market trades
            mt = state.market_trades.get(product, [])
            market_log[product] = serialize_trades(mt)
            stats_log[product] = trade_stats(mt)

            # Log own trades
            ot = state.own_trades.get(product, [])
            own_log[product] = serialize_trades(ot)

            orders: List[Order] = []
            mid = compute_mid(depth)

            # --- Flatten if position too large ---
            if abs(position) > FLATTEN_THRESHOLD:
                if mid is not None:
                    if position > 0:
                        flatten_price = math.floor(mid)
                        qty = -min(position, PROBE_QTY * 2)
                        o = safe_order(product, flatten_price, qty, position)
                        if o:
                            orders.append(o)
                    else:
                        flatten_price = math.ceil(mid)
                        qty = min(abs(position), PROBE_QTY * 2)
                        o = safe_order(product, flatten_price, qty, position)
                        if o:
                            orders.append(o)

            # --- Place probe order ---
            elif mid is not None and products[probe_product_idx % len(products)] == product:
                offset = PROBE_OFFSETS[probe_idx % len(PROBE_OFFSETS)]
                probe_price = round(mid) + offset

                # Decide side: buy if offset <= 0 (trying to buy cheap), sell if offset > 0
                # But if position is positive, prefer sell probes; if negative, prefer buy probes
                if position > FLATTEN_THRESHOLD // 2:
                    side = "sell"
                elif position < -(FLATTEN_THRESHOLD // 2):
                    side = "buy"
                elif offset <= 0:
                    side = "buy"
                else:
                    side = "sell"

                qty = PROBE_QTY if side == "buy" else -PROBE_QTY
                o = safe_order(product, probe_price, qty, position)
                if o:
                    orders.append(o)
                    probe_info = {
                        "product": product,
                        "side": side,
                        "price": probe_price,
                        "offset": offset,
                        "mid": round(mid, 1),
                        "t": state.timestamp,
                    }
                    # Compact keys for traderData storage
                    saved["pp"] = {
                        "pr": product, "sd": side, "px": probe_price,
                        "o": offset, "t": state.timestamp,
                    }

                # Advance probe schedule
                probe_idx += 1
                saved["probe_idx"] = probe_idx

            result[product] = orders

        # Rotate probe product
        probe_product_idx += 1
        saved["probe_product_idx"] = probe_product_idx

        # Summarize fill history (compact key "f" = filled)
        total_probes = len(fill_history)
        total_fills = sum(1 for entry in fill_history if entry.get("f"))
        fill_rate = round(total_fills / total_probes, 3) if total_probes > 0 else None

        print("SPY_TRADE|" + json.dumps({
            "t": state.timestamp,
            "tick": tick,
            "pos": positions,
            "market": market_log,
            "own": own_log,
            "stats": stats_log,
            "probe": probe_info,
            "prev_fill": prev_fill,
            "fill_summary": {
                "total": total_probes,
                "fills": total_fills,
                "rate": fill_rate,
            },
        }))

        return result, 0, self._save_state(saved)


# ======================================================================
# Local smoke test
# ======================================================================
if __name__ == "__main__":
    from datamodel import Listing, Observation, Trade

    def make_state(products_data, timestamp=100, td="",
                   market_trades=None, own_trades=None):
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
            own_trades=own_trades or {p: [] for p in products_data},
            market_trades=market_trades or {p: [] for p in products_data},
            position=positions,
            observations=Observation({}, {}),
        )

    t = Trader()
    print("=" * 60)
    print("SPY_TRADES SMOKE TEST")
    print("=" * 60)

    ACO = "ASH_COATED_OSMIUM"
    IPR = "INTARIAN_PEPPER_ROOT"

    print("\n--- Tick 1: Market trades present, probe placed ---")
    mt = {
        ACO: [
            Trade(symbol=ACO, price=10001, quantity=5, buyer="", seller="", timestamp=0),
            Trade(symbol=ACO, price=10002, quantity=3, buyer="", seller="", timestamp=0),
        ],
        IPR: [],
    }
    s = make_state(
        {
            ACO: ({9998: 10, 9996: 20}, {10002: -10, 10004: -20}, 0),
            IPR: ({11990: 15, 11988: 20}, {12010: -15, 12012: -20}, 0),
        },
        timestamp=0, market_trades=mt,
    )
    r, _, td = t.run(s)
    print(f"  Orders: {r}")

    print("\n--- Tick 2: Previous probe result checked ---")
    s = make_state(
        {
            ACO: ({9998: 10, 9996: 20}, {10002: -10, 10004: -20}, 1),
            IPR: ({11990: 15, 11988: 20}, {12010: -15, 12012: -20}, 0),
        },
        timestamp=100, td=td,
    )
    r, _, td = t.run(s)
    print(f"  Orders: {r}")

    print("\n--- Tick 3: Position at 15 -> should flatten ---")
    s = make_state(
        {
            ACO: ({9998: 10, 9996: 20}, {10002: -10, 10004: -20}, 15),
            IPR: ({11990: 15, 11988: 20}, {12010: -15, 12012: -20}, 0),
        },
        timestamp=200, td=td,
    )
    r, _, td = t.run(s)
    print(f"  Orders: {r}")

    print("\n--- Position limit check: pos=79 ---")
    s = make_state(
        {
            ACO: ({9998: 10, 9996: 20}, {10002: -10, 10004: -20}, 79),
            IPR: ({11990: 15, 11988: 20}, {12010: -15, 12012: -20}, 0),
        },
        timestamp=300, td=td,
    )
    r, _, td = t.run(s)
    for product, orders in r.items():
        pos = 79 if product == ACO else 0
        for o in orders:
            new_pos = pos + o.quantity
            assert abs(new_pos) <= POSITION_LIMIT, f"Position limit breached for {product}: {new_pos}"
    print("  Position limit OK")
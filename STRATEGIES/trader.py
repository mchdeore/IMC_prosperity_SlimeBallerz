"""
Prosperity-style trader (see "Writing an Algorithm in Python").

Imports follow the guide’s example (`datamodel`, `typing`, `string`); `jsonpickle`
is for `traderData` as in the guide’s Lambda / persistence section.
"""

from datamodel import OrderDepth, UserId, TradingState, Order  # noqa: F401
from typing import Dict, List

import string  # noqa: F401 — in the official example; unused in this toy strategy

import jsonpickle


class Trader:
    """
    Toy strategy: on each `run` callback, either buy 1 at the best ask or sell 1
    at the best bid, alternating per product. Phase is stored in traderData so it
    survives between simulation iterations (same pattern as the guide’s jsonpickle note).
    """

    def bid(self) -> int:
        # Round 2 only in the competition; harmless stub for other rounds.
        return 0

    # -------------------------------------------------------------------------
    # traderData (str) <-> in-memory dict
    # -------------------------------------------------------------------------
    # Guide: serialize *any* Python value to a string with jsonpickle, pass it back
    # as the third return value; next call receives it as state.traderData.
    # Shape we store: {"phases": {product_name: "buy" | "sell", ...}}
    # -------------------------------------------------------------------------

    def _load_phases(self, trader_data: str) -> Dict[str, str]:
        """
        trader_data: str
            Raw string from the previous `run` (TradingState.traderData). May be "".

        returns: Dict[str, str]
            product (str) -> next action: "buy" or "sell".
        """
        if not trader_data:
            return {}
        try:
            # jsonpickle.decode(str) -> Python object (here: dict)
            blob = jsonpickle.decode(trader_data)
            if not isinstance(blob, dict):
                return {}
            phases = blob.get("phases", {})
            if not isinstance(phases, dict):
                return {}
            # values should be "buy" | "sell"; coerce missing keys per product later
            return {str(k): str(v) for k, v in phases.items()}
        except Exception:
            return {}

    def _save_phases(self, phases: Dict[str, str]) -> str:
        """
        phases: Dict[str, str]
            product -> "buy" | "sell"

        returns: str
            Must fit in traderData budget (guide: platform may truncate ~50k chars).
        """
        return jsonpickle.encode({"phases": phases})

    # -------------------------------------------------------------------------
    # Order book helpers (OrderDepth from datamodel / guide)
    # -------------------------------------------------------------------------
    # depth.buy_orders:  Dict[int, int]  — price -> **positive** resting bid size
    # depth.sell_orders: Dict[int, int]  — price -> **negative** resting ask size
    #   Example sell_orders {12: -3, 11: -2} => 3 units offered at 12, 2 at 11.
    # Best bid = highest buy price; best ask = lowest sell price.
    # -------------------------------------------------------------------------

    def _best_ask(self, depth: OrderDepth):
        """
        returns: tuple[int, int] | None
            (ask_price, raw_level_value) where raw_level_value is negative per spec,
            or None if no asks.
        """
        if not depth.sell_orders:
            return None
        price = min(depth.sell_orders.keys())
        return price, depth.sell_orders[price]

    def _best_bid(self, depth: OrderDepth):
        """
        returns: tuple[int, int] | None
            (bid_price, positive_size_at_level), or None if no bids.
        """
        if not depth.buy_orders:
            return None
        price = max(depth.buy_orders.keys())
        return price, depth.buy_orders[price]

    def run(self, state: TradingState):
        """
        state: TradingState  (full snapshot for this simulation tick)
            .traderData: str
                Our own persisted blob from the *previous* tick (or "").
            .timestamp: int
                Exchange time for this tick.
            .listings: Dict[Symbol, Listing]
                Metadata per symbol (symbol, product, denomination).
            .order_depths: Dict[Symbol, OrderDepth]
                Per symbol: resting **other participants’** quotes we may trade with.
            .own_trades: Dict[Symbol, List[Trade]]
                Fills **we** got since last TradingState.
            .market_trades: Dict[Symbol, List[Trade]]
                Everyone else’s prints since last TradingState.
            .position: Dict[Product, Position]  (Position is int)
                Signed inventory: long > 0, short < 0, flat 0.
            .observations: Observation
                Optional signals / conversion hints (often unused in simple bots).

        returns: tuple[ dict, int|None, str ]
            (
              result,       # Dict[Product, List[Order]] — orders to send this tick
              conversions,  # int or None — conversion request count (0/None = none)
              traderData,   # str — serialized state for the *next* tick
            )

        Order(symbol, price, quantity):
            quantity > 0  => BUY  up to `price`
            quantity < 0  => SELL down to `price` (size is abs(quantity))
        """
        # Restore alternating step per product from last call.
        phases: Dict[str, str] = self._load_phases(state.traderData)

        # result maps every symbol we touch this tick -> list of Order objects
        result: Dict[str, List[Order]] = {}

        # state.order_depths: Dict[str, OrderDepth]
        for product, depth in state.order_depths.items():
            orders: List[Order] = []

            # Default first visit: try to buy (then we flip to "sell" after placing buy).
            phase = phases.get(product, "buy")

            # state.position: Dict[Product, int]
            pos: int = state.position.get(product, 0)

            if phase == "buy":
                ask = self._best_ask(depth)
                if ask is not None:
                    price, raw_ask_size = ask
                    # raw_ask_size is negative in the book; we only need price to cross.
                    # Buy 1: positive quantity per guide.
                    orders.append(Order(product, price, 1))
                    phases[product] = "sell"
                # If no asks, leave phase as "buy" and send no orders.
            else:
                # Sell only if we actually hold inventory (guide: position is signed int).
                if pos <= 0:
                    phases[product] = "buy"
                else:
                    bid = self._best_bid(depth)
                    if bid is not None:
                        price, bid_size = bid
                        # Sell 1: negative quantity per guide.
                        orders.append(Order(product, price, -1))
                        phases[product] = "buy"
                    # If no bids but long, stay on "sell" for a later tick.

            result[product] = orders

        conversions = 0  # int — set if using Observation / conversions (guide); else 0/None
        trader_data_out = self._save_phases(phases)
        return result, conversions, trader_data_out


if __name__ == "__main__":
    from datamodel import Listing, Observation, Trade

    od = OrderDepth()
    od.buy_orders = {99: 5}
    od.sell_orders = {101: -5}
    listing = Listing(symbol="TEST", product="TEST", denomination="XIRECS")
    empty_obs = Observation({}, {})
    state = TradingState(
        traderData="",
        timestamp=1,
        listings={"TEST": listing},
        order_depths={"TEST": od},
        own_trades={"TEST": []},
        market_trades={"TEST": []},
        position={"TEST": 0},
        observations=empty_obs,
    )
    t = Trader()
    r1, c1, d1 = t.run(state)
    print("t=1 buy:", r1, "traderData", d1)
    state2 = TradingState(
        traderData=d1,
        timestamp=2,
        listings={"TEST": listing},
        order_depths={"TEST": od},
        own_trades={"TEST": []},
        market_trades={"TEST": []},
        position={"TEST": 1},
        observations=empty_obs,
    )
    r2, c2, d2 = t.run(state2)
    print("t=2 sell:", r2, "traderData", d2)

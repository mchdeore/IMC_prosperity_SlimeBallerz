"""
Spy: Observation Data Capturer
===============================
Captures all observation data every tick -- plainValueObservations and
conversionObservations (sunlight, humidity, conversion prices, tariffs).
Tracks deltas between ticks to spot jumps.

Optionally probes conversions: places a single conversion request every
CONVERT_INTERVAL ticks to measure the actual cost, then logs the PnL
impact.

Deploy on the Prosperity simulator and parse the log with log_parser.py.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import json

POSITION_LIMIT = 80
CONVERT_INTERVAL = 50  # ticks between conversion probes
CONVERT_QTY = 1
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


def extract_conv_obs(obs) -> dict:
    """Pull all fields from a ConversionObservation into a plain dict."""
    d = {}
    for attr in ("bidPrice", "askPrice", "transportFees",
                 "exportTariff", "importTariff"):
        d[attr] = getattr(obs, attr, None)
    # These field names vary across Prosperity versions
    for attr in ("sugarPrice", "sunlight", "sunlightIndex",
                 "humidity", "humidityIndex"):
        val = getattr(obs, attr, None)
        if val is not None:
            d[attr] = val
    return d


def compute_deltas(current: dict, previous: dict) -> dict:
    """Compute field-level deltas between two observation snapshots."""
    deltas = {}
    for key in current:
        if key in previous and current[key] is not None and previous[key] is not None:
            try:
                deltas[key] = round(current[key] - previous[key], 6)
            except (TypeError, ValueError):
                pass
    return deltas


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
            for key in ("prev_conv", "prev_plain"):
                state.pop(key, None)
            s = json.dumps(state)
        return s

    def run(self, state: TradingState):
        saved = self._load_state(state.traderData)
        tick = saved.get("tick", 0) + 1
        saved["tick"] = tick

        result: Dict[str, List[Order]] = {}
        for product in state.order_depths:
            result[product] = []

        positions = {p: state.position.get(p, 0) for p in state.order_depths}

        # --- Extract plain observations ---
        plain = {}
        try:
            if hasattr(state.observations, "plainValueObservations"):
                raw = state.observations.plainValueObservations
                if raw:
                    plain = {str(k): v for k, v in raw.items()}
        except Exception:
            pass

        # --- Extract conversion observations ---
        conv: Dict[str, dict] = {}
        try:
            if hasattr(state.observations, "conversionObservations"):
                raw = state.observations.conversionObservations
                if raw:
                    for prod, obs in raw.items():
                        conv[str(prod)] = extract_conv_obs(obs)
        except Exception:
            pass

        # --- Compute deltas from previous tick ---
        prev_plain = saved.get("prev_plain", {})
        prev_conv = saved.get("prev_conv", {})

        plain_deltas = compute_deltas(plain, prev_plain)
        conv_deltas = {}
        for prod in conv:
            if prod in prev_conv:
                conv_deltas[prod] = compute_deltas(conv[prod], prev_conv[prod])

        saved["prev_plain"] = plain
        saved["prev_conv"] = conv

        # --- Conversion probe state machine ---
        # States: IDLE -> ACQUIRE -> CONVERT -> MEASURE -> IDLE
        probe_state = saved.get("probe_state", "IDLE")
        probe_info = None
        conversions = 0

        if conv:
            probe_product = list(conv.keys())[0]
            probe_pos = positions.get(probe_product, 0)

            if probe_state == "IDLE" and tick % CONVERT_INTERVAL == 0:
                # Need a position to convert. Buy 1 unit first.
                depth = state.order_depths.get(probe_product)
                if depth and depth.sell_orders:
                    best_ask = min(depth.sell_orders)
                    o = safe_order(probe_product, best_ask, CONVERT_QTY, probe_pos)
                    if o:
                        result[probe_product] = [o]
                        saved["probe_state"] = "ACQUIRE"
                        saved["probe_product"] = probe_product
                        saved["probe_pre_pnl"] = None
                        probe_info = {"phase": "ACQUIRE", "product": probe_product}

            elif probe_state == "ACQUIRE":
                # We should now hold a position from the buy; request conversion
                if probe_pos != 0:
                    conversions = min(abs(probe_pos), CONVERT_QTY)
                    if probe_pos < 0:
                        conversions = -conversions
                    saved["probe_state"] = "CONVERT"
                    probe_info = {"phase": "CONVERT", "conversions": conversions}
                else:
                    saved["probe_state"] = "IDLE"

            elif probe_state == "CONVERT":
                # Conversion happened last tick; measure impact
                saved["probe_state"] = "MEASURE"
                probe_info = {
                    "phase": "MEASURE",
                    "post_position": probe_pos,
                    "conv_obs": conv.get(saved.get("probe_product", ""), {}),
                }

            elif probe_state == "MEASURE":
                saved["probe_state"] = "IDLE"
                probe_info = {"phase": "DONE"}

        # --- Flatten any residual position from probes ---
        for product in state.order_depths:
            pos = positions.get(product, 0)
            if abs(pos) > 0 and probe_state in ("IDLE", "MEASURE"):
                depth = state.order_depths[product]
                if pos > 0 and depth.buy_orders:
                    best_bid = max(depth.buy_orders)
                    o = safe_order(product, best_bid, -min(pos, CONVERT_QTY), pos)
                    if o:
                        result.setdefault(product, []).append(o)
                elif pos < 0 and depth.sell_orders:
                    best_ask = min(depth.sell_orders)
                    o = safe_order(product, best_ask, min(abs(pos), CONVERT_QTY), pos)
                    if o:
                        result.setdefault(product, []).append(o)

        print("SPY_OBS|" + json.dumps({
            "t": state.timestamp,
            "tick": tick,
            "pos": positions,
            "plain": plain,
            "conv": conv,
            "deltas_plain": plain_deltas,
            "deltas_conv": conv_deltas,
            "probe": probe_info,
        }))

        return result, conversions, self._save_state(saved)


# ======================================================================
# Local smoke test
# ======================================================================
if __name__ == "__main__":
    from datamodel import Listing, Observation

    try:
        from datamodel import ConversionObservation
    except ImportError:
        class ConversionObservation:
            def __init__(self, bidPrice, askPrice, transportFees,
                         exportTariff, importTariff, sunlight, humidity):
                self.bidPrice = bidPrice
                self.askPrice = askPrice
                self.transportFees = transportFees
                self.exportTariff = exportTariff
                self.importTariff = importTariff
                self.sunlight = sunlight
                self.humidity = humidity

    def make_state(products_data, timestamp=100, td="",
                   plain_obs=None, conv_obs=None):
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
            observations=Observation(plain_obs or {}, conv_obs or {}),
        )

    t = Trader()
    print("=" * 60)
    print("SPY_OBSERVATIONS SMOKE TEST")
    print("=" * 60)

    conv = {
        "SUGAR": ConversionObservation(
            bidPrice=3000, askPrice=3010, transportFees=50,
            exportTariff=10, importTariff=20, sunlight=1200.5, humidity=65.3,
        )
    }

    print("\n--- Tick 1: With observations, no conversion yet ---")
    s = make_state(
        {"ASH_COATED_OSMIUM": ({9998: 10}, {10002: -10}, 0)},
        timestamp=0,
        plain_obs={"HUMIDITY": 65.3, "SUNLIGHT": 1200.5},
        conv_obs=conv,
    )
    r, c, td = t.run(s)
    print(f"  Orders: {r}, Conversions: {c}")

    print("\n--- Tick 2: Observations changed -> deltas logged ---")
    conv2 = {
        "SUGAR": ConversionObservation(
            bidPrice=3005, askPrice=3015, transportFees=50,
            exportTariff=10, importTariff=20, sunlight=1210.0, humidity=64.8,
        )
    }
    s = make_state(
        {"ASH_COATED_OSMIUM": ({9998: 10}, {10002: -10}, 0)},
        timestamp=100, td=td,
        plain_obs={"HUMIDITY": 64.8, "SUNLIGHT": 1210.0},
        conv_obs=conv2,
    )
    r, c, td = t.run(s)
    print(f"  Orders: {r}, Conversions: {c}")

    print("\n--- Position limit check ---")
    s = make_state(
        {"ASH_COATED_OSMIUM": ({9998: 10}, {10002: -10}, 79)},
        timestamp=200, td=td,
    )
    r, c, td = t.run(s)
    for product, orders in r.items():
        for o in orders:
            new_pos = 79 + o.quantity
            assert abs(new_pos) <= POSITION_LIMIT, f"Position limit breached: {new_pos}"
    print("  Position limit OK")

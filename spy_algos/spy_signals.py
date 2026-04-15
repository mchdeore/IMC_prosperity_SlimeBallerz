"""
Spy: Derived Signals & Regime Classifier
==========================================
Purely passive -- no trading. Computes and logs rich derived signals
every tick that go beyond what the CSV provides:

  mid, VWMP, spread, imbalance, wall_mid, microprice, book_pressure

Plus rolling statistics persisted across ticks:
  EMA of returns, realized volatility, lag-1 autocorrelation,
  regime classification (mean-reverting / trending / random walk).

Deploy on the Prosperity simulator and parse the log with log_parser.py.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import json
import math

POSITION_LIMIT = 80
TRADER_DATA_LIMIT = 45_000  # safety margin below the 50K hard cap

# Rolling stats config
EMA_ALPHA = 0.1
VOL_WINDOW = 10
AC_THRESHOLD = 0.05  # |lag1_ac| above this = non-random


def compute_signals(depth: OrderDepth) -> Optional[dict]:
    """Compute all instantaneous signals from an OrderDepth."""
    bids = sorted(depth.buy_orders.items(), reverse=True)
    asks = sorted(depth.sell_orders.items())

    if not bids or not asks:
        return None

    best_bid, best_bid_vol = bids[0]
    best_ask, best_ask_vol_neg = asks[0]
    best_ask_vol = abs(best_ask_vol_neg)

    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid

    # VWMP: volume-weighted mid-price across all levels
    total_pv = 0.0
    total_v = 0.0
    for p, v in bids:
        total_pv += p * v
        total_v += v
    for p, v in asks:
        av = abs(v)
        total_pv += p * av
        total_v += av
    vwmp = total_pv / total_v if total_v > 0 else mid

    # Order imbalance
    bid_vol = sum(v for _, v in bids)
    ask_vol = sum(abs(v) for _, v in asks)
    denom = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / denom if denom > 0 else 0.0

    # Wall mid: average of deepest-volume bid and ask prices
    bid_wall_price = max(bids, key=lambda x: x[1])[0]
    ask_wall_price = min(asks, key=lambda x: abs(x[1]))[0]
    # Actually want the max volume on ask side too
    ask_wall_price = max(asks, key=lambda x: abs(x[1]))[0]
    wall_mid = (bid_wall_price + ask_wall_price) / 2.0

    # Microprice: size-weighted fair value tilting toward the thinner side
    vol_sum = best_bid_vol + best_ask_vol
    if vol_sum > 0:
        microprice = (best_bid * best_ask_vol + best_ask * best_bid_vol) / vol_sum
    else:
        microprice = mid

    # Book pressure: top-2 bid volume / top-2 ask volume
    top2_bid_vol = sum(v for _, v in bids[:2])
    top2_ask_vol = sum(abs(v) for _, v in asks[:2])
    book_pressure = top2_bid_vol / top2_ask_vol if top2_ask_vol > 0 else float("inf")

    return {
        "mid": round(mid, 2),
        "vwmp": round(vwmp, 2),
        "spread": spread,
        "imbalance": round(imbalance, 4),
        "wall_mid": round(wall_mid, 2),
        "microprice": round(microprice, 2),
        "book_pressure": round(book_pressure, 4),
        "bid_depth": bid_vol,
        "ask_depth": ask_vol,
        "bid_levels": len(bids),
        "ask_levels": len(asks),
    }


def update_rolling(saved_product: dict, mid: float) -> dict:
    """
    Update rolling statistics for one product. Returns the rolling dict
    to be included in the log output.
    """
    prev_mid = saved_product.get("prev_mid")
    returns = saved_product.get("returns", [])
    ema_ret = saved_product.get("ema_ret", 0.0)

    if prev_mid is not None and prev_mid != 0:
        ret = (mid - prev_mid) / prev_mid
    else:
        ret = 0.0

    # Update EMA of returns
    ema_ret = EMA_ALPHA * ret + (1 - EMA_ALPHA) * ema_ret
    saved_product["ema_ret"] = ema_ret

    # Append return, keep window
    returns.append(ret)
    if len(returns) > VOL_WINDOW:
        returns = returns[-VOL_WINDOW:]
    saved_product["returns"] = returns

    saved_product["prev_mid"] = mid

    # Realized volatility
    if len(returns) >= 3:
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        volatility = math.sqrt(var)
    else:
        volatility = None

    # Lag-1 autocorrelation
    lag1_ac = None
    if len(returns) >= 4:
        n = len(returns)
        mean_r = sum(returns) / n
        numer = sum(
            (returns[i] - mean_r) * (returns[i - 1] - mean_r)
            for i in range(1, n)
        )
        denom = sum((r - mean_r) ** 2 for r in returns)
        if denom > 0:
            lag1_ac = numer / denom

    # Regime classification
    regime = "RW"
    if lag1_ac is not None:
        if lag1_ac < -AC_THRESHOLD:
            regime = "MR"
        elif lag1_ac > AC_THRESHOLD:
            regime = "TR"

    return {
        "ema_return": round(ema_ret, 6),
        "volatility": round(volatility, 6) if volatility is not None else None,
        "lag1_ac": round(lag1_ac, 4) if lag1_ac is not None else None,
        "regime": regime,
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
            print(f"WARNING: traderData {len(s)} chars, trimming rolling buffers")
            for key in list(state):
                if key.startswith("rolling_"):
                    state[key]["returns"] = state[key]["returns"][-3:]
            s = json.dumps(state)
        return s

    def run(self, state: TradingState):
        saved = self._load_state(state.traderData)
        tick = saved.get("tick", 0) + 1
        saved["tick"] = tick

        result: Dict[str, List[Order]] = {}
        signals_log: Dict[str, dict] = {}
        rolling_log: Dict[str, dict] = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            result[product] = []  # no trading

            sigs = compute_signals(depth)
            if sigs is not None:
                signals_log[product] = sigs

                # Update rolling stats
                prod_state = saved.setdefault(f"rolling_{product}", {})
                rolling = update_rolling(prod_state, sigs["mid"])
                rolling_log[product] = rolling
            else:
                signals_log[product] = {"mid": None, "error": "one_sided_book"}

        print("SPY_SIG|" + json.dumps({
            "t": state.timestamp,
            "tick": tick,
            "signals": signals_log,
            "rolling": rolling_log,
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
    print("SPY_SIGNALS SMOKE TEST")
    print("=" * 60)

    ACO = "ASH_COATED_OSMIUM"
    IPR = "INTARIAN_PEPPER_ROOT"

    td = ""
    for i in range(12):
        # Simulate small price drift
        aco_mid_base = 10000 + i * 2
        ipr_mid_base = 12000 - i * 3

        print(f"\n--- Tick {i + 1} ---")
        s = make_state({
            ACO: (
                {aco_mid_base - 2: 10, aco_mid_base - 4: 20, aco_mid_base - 6: 15},
                {aco_mid_base + 2: -10, aco_mid_base + 4: -20, aco_mid_base + 6: -15},
                0,
            ),
            IPR: (
                {ipr_mid_base - 3: 17, ipr_mid_base - 6: 21},
                {ipr_mid_base + 3: -11, ipr_mid_base + 6: -21},
                0,
            ),
        }, timestamp=i * 100, td=td)
        r, _, td = t.run(s)

    # Verify no orders emitted
    for product, orders in r.items():
        assert len(orders) == 0, f"Signals spy should not trade, but got orders for {product}"
    print("\n  No-trade assertion OK")

    # Verify rolling state was built
    final_state = json.loads(td)
    assert final_state["tick"] == 12
    print(f"  Final tick: {final_state['tick']}")
    for key in final_state:
        if key.startswith("rolling_"):
            prod = key.replace("rolling_", "")
            print(f"  {prod} rolling returns buffer: {len(final_state[key].get('returns', []))} entries")

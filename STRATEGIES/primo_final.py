"""
Primo FINAL - simplest possible writeup with best-found parameters.
====================================================================

This is primo_v3 rewritten as flat, beginner-readable code with the
best parameters from the primo_exploration suite HARDCODED, plus a
simple watermark-drawdown bail that drops IPR into "safe maker mode"
if the upward drift breaks.

HARDCODED PARAMETERS (all from results/primo_exploration/ sweeps):

    ASH_COATED_OSMIUM (ACO):
        POSITION_LIMIT       = 80           # exchange hard cap
        ACO_SOFT_CAP         = 76           # conservative safety margin (user choice)
        ACO_MAKE_PORTION     = 0.80         # use 80% of remaining capacity
        ACO_BID_FRAC         = 0.50         # even split
        ACO_ASK_FRAC         = 0.50
        ACO_MAKE_BEAT_TICKS  = 1            # bid = best_bid + 1
        ACO_MIN_TAKE_EDGE    = 1            # only take >= 1 tick edge
        ACO_FAIR_LEVELS      = [2, 3]       # from test_03 (exclude L1)
        ACO_EMA_ALPHA_NEW    = 0.05         # from test_03 (slow EMA)
        ACO_ANCHOR           = 10000        # mid-reverts around here

    INTARIAN_PEPPER_ROOT (IPR) - primary drift mode:
        IPR_SOFT_CAP         = 70           # LINEAR mode: flatten/backoff sooner than ACO
        IPR_MAKE_PORTION     = 0.90         # higher than ACO
        IPR_BID_FRAC         = 0.70         # asymmetric: more volume on bid
        IPR_ASK_FRAC         = 0.30
        IPR_MAKE_BEAT_TICKS  = 1
        IPR_MIN_TAKE_EDGE    = 1            # symmetric take edge
        IPR_LONG_TAKE_EDGE   = -5           # from test_09 (BIGGEST WIN)
                                            # buy any ask priced up to fair+5
        IPR_SLOPE            = 0.0012       # from tests 02+12 (defensive)
        IPR_QUOTE_BIAS_TICKS = 2            # long-lean applied to both quotes
        IPR_BIAS_CLAMP       = True         # clamp bid below fair (safety)
        IPR_ONE_SIDED_VOLUME_FRAC = 0.5     # maker size when L1 missing on that side
        # LINEAR maker: symmetric backoff past soft cap (like ACO), not short-only.

    INTARIAN_PEPPER_ROOT (IPR) - bail trigger:
        IPR_BAIL_DRAWDOWN    = 30           # ticks below peak mid to alarm
        IPR_BAIL_CONSEC      = 5            # consecutive ticks below threshold

        If the IPR mid drops more than 30 ticks below its highest observed
        value and stays there for 5 consecutive ticks, we permanently switch
        to SAFE mode. Once latched, we do not switch back.

    INTARIAN_PEPPER_ROOT (IPR) - SAFE mode (fallback market maker):
        IPR_SAFE_MAKE_PORTION    = 0.50     # reduced volume (risk off)
        IPR_SAFE_MIN_TAKE_EDGE   = 1        # symmetric taker, no long bias
        IPR_SAFE_MAKE_BEAT_TICKS = -2       # BACK OFF from best by 2 ticks

        SAFE fair is L2+L3 book average (same as _ipr_mid_l2_l3), falling
        back to L1 mid if shallow. No slope assumption; symmetric 50/50
        volume split, always flattens toward 0, and posts BACKED-OFF
        maker quotes so fills only happen on aggressive flow. Validated
        that this avoids ~9k/day loss if bail false-fires on a drifting
        market (symmetric beat-by-1 quotes would accumulate wrong-side
        inventory into the drift).

All values are hardcoded as module-level constants. No dicts, no env
vars, no overrides.
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
IPR = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80


# =======================================================================
# ACO HARDCODED PARAMETERS (from test_03_aco_sweep.py winner)
# =======================================================================

ACO_SOFT_CAP        = 76
ACO_MAKE_PORTION    = 0.80
ACO_BID_FRAC        = 0.50
ACO_ASK_FRAC        = 0.50
ACO_MAKE_BEAT_TICKS = 1
ACO_MIN_TAKE_EDGE   = 1
ACO_FAIR_LEVELS     = [2, 3]
ACO_EMA_ALPHA_NEW   = 0.05
ACO_ANCHOR          = 10000


# =======================================================================
# IPR HARDCODED PARAMETERS (from tests 02, 09, 12)
# =======================================================================

IPR_SOFT_CAP         = 70
IPR_MAKE_PORTION     = 0.90
IPR_BID_FRAC         = 0.70
IPR_ASK_FRAC         = 0.30
IPR_MAKE_BEAT_TICKS  = 1
IPR_MIN_TAKE_EDGE    = 1
IPR_LONG_TAKE_EDGE   = -5
IPR_SLOPE            = 0.0012
IPR_QUOTE_BIAS_TICKS = 2
IPR_BIAS_CLAMP       = True
IPR_ONE_SIDED_VOLUME_FRAC = 0.5  # scale maker vol when no best_bid / no best_ask touch

# Bail trigger: if mid drops this many ticks below peak for this many
# consecutive ticks, switch to SAFE mode (latched, no switching back).
IPR_BAIL_DRAWDOWN    = 30
IPR_BAIL_CONSEC      = 5

# SAFE mode parameters (simple symmetric market maker, no drift bet)
IPR_SAFE_MAKE_PORTION    = 0.50
IPR_SAFE_MIN_TAKE_EDGE   = 1
IPR_SAFE_MAKE_BEAT_TICKS = -2    # BACK OFF from best by 2 ticks. This makes
                                 # SAFE quotes "inactive" most of the time -
                                 # they only fill on big aggressive flow.
                                 # Validated: prevents ~9k/day loss if bail
                                 # false-fires on a still-drifting market.


# =======================================================================
# TRADER
# =======================================================================

class Trader:
    def __init__(
        self,
        tick_recorder: Optional[TickRecorder] = None,
        record_ticks: bool = True,
        sandbox_stdout: Optional[bool] = None,
    ):
        """
        tick_recorder: optional custom TickRecorder; if None and record_ticks,
        writes ``LOGS/primo_final_ticks_<datetime>.csv`` (merge with backtest
        activities log on timestamp + product). Set record_ticks=False for
        competition submission to skip file I/O.

        sandbox_stdout: if True, print one JSON line per tick so the backtester's
        ``lambdaLog`` is populated (otherwise Sandbox logs are only timestamps).
        Defaults to the same value as record_ticks when omitted.
        """
        if tick_recorder is not None:
            self.tick_recorder = tick_recorder
        elif record_ticks:
            self.tick_recorder = TickRecorder(auto_save_csv=logs_csv_path("primo_final_ticks"))
        else:
            self.tick_recorder = None

        if sandbox_stdout is None:
            sandbox_stdout = record_ticks
        self._sandbox_stdout = sandbox_stdout

    def run(self, state: TradingState):
        # Load persisted trader state.
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
            elif product == IPR:
                orders = self.run_ipr(depth, position, state.timestamp, saved)
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

    # -------------------------------------------------------------------
    # ACO STRATEGY
    # -------------------------------------------------------------------

    def run_aco(self, depth, position, timestamp, saved):
        aco_state = saved.setdefault("aco", {})

        # ---- 1. Fair value: L2+L3 mid per side, slow EMA ----
        previous_fair = aco_state.get("fair")
        book_fair = None

        sorted_bids = sorted(depth.buy_orders.keys(), reverse=True)
        sorted_asks = sorted(depth.sell_orders.keys())

        if sorted_bids and sorted_asks:
            bid_prices = []
            for level in ACO_FAIR_LEVELS:
                if level - 1 < len(sorted_bids):
                    bid_prices.append(sorted_bids[level - 1])
            ask_prices = []
            for level in ACO_FAIR_LEVELS:
                if level - 1 < len(sorted_asks):
                    ask_prices.append(sorted_asks[level - 1])
            if bid_prices and ask_prices:
                bid_mid = sum(bid_prices) / len(bid_prices)
                ask_mid = sum(ask_prices) / len(ask_prices)
                book_fair = (bid_mid + ask_mid) / 2.0

        # book_fair stays None when either side has fewer than 3 price levels
        # (ACO_FAIR_LEVELS [2,3] need indices 1 and 2) — fair falls back below.

        if book_fair is not None and previous_fair is not None:
            fair = ACO_EMA_ALPHA_NEW * book_fair + (1 - ACO_EMA_ALPHA_NEW) * previous_fair
        elif book_fair is not None:
            fair = book_fair
        elif previous_fair is not None:
            fair = previous_fair
        else:
            fair = float(ACO_ANCHOR)

        aco_state["fair"] = fair

        buy_capacity  = POSITION_LIMIT - position
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

        # Start with beat-by-1
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

        # Volume sizing
        bid_volume = int(buy_capacity * ACO_MAKE_PORTION * ACO_BID_FRAC / 0.5)
        ask_volume = int(sell_capacity * ACO_MAKE_PORTION * ACO_ASK_FRAC / 0.5)
        bid_volume = min(bid_volume, buy_capacity)
        ask_volume = min(ask_volume, sell_capacity)

        # Symmetric pressure when over softcap
        if abs(position) > ACO_SOFT_CAP:
            excess = (abs(position) - ACO_SOFT_CAP) / float(POSITION_LIMIT - ACO_SOFT_CAP)
            if excess > 1.0:
                excess = 1.0
            if position > 0:
                # Long: pull ask toward fair, shrink bid
                shift = int(round(excess * (ask_price - fair)))
                new_ask = ask_price - shift
                if new_ask < min_safe_ask:
                    new_ask = min_safe_ask
                ask_price = new_ask
                bid_volume = int(bid_volume * (1.0 - excess))
            elif position < 0:
                # Short: pull bid toward fair, shrink ask
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

    @staticmethod
    def _ipr_mid_l2_l3(depth: OrderDepth) -> float:
        """
        Mid from the mean of L2+L3 bid prices and L2+L3 ask prices (four levels),
        same construction as optimized_submission ACO book fair. Shallow books
        fall back to fewer levels, then L1 mid if needed.
        """
        bids = sorted(depth.buy_orders.keys(), reverse=True)
        asks = sorted(depth.sell_orders.keys())
        if not bids or not asks:
            return float("nan")

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

    # -------------------------------------------------------------------
    # IPR DISPATCHER + BAIL CHECK
    # -------------------------------------------------------------------
    #
    # We start in "LINEAR" mode (drift-based, long-biased). Each tick we
    # check the market mid vs the highest mid we've ever seen today. If
    # the mid has fallen more than IPR_BAIL_DRAWDOWN ticks below the peak
    # for IPR_BAIL_CONSEC consecutive ticks, we latch into "SAFE" mode:
    # a simple symmetric market maker that assumes nothing about drift.
    # Once SAFE, we stay SAFE.
    # -------------------------------------------------------------------

    def run_ipr(self, depth, position, timestamp, saved):
        ipr_state = saved.setdefault("ipr", {"mode": "LINEAR"})

        # Update drawdown watermark + counter for the bail trigger
        if depth.buy_orders and depth.sell_orders:
            current_mid = self._ipr_mid_l2_l3(depth)
            if math.isnan(current_mid):
                best_bid = max(depth.buy_orders)
                best_ask = min(depth.sell_orders)
                current_mid = (best_bid + best_ask) / 2.0

            peak = ipr_state.get("peak_mid")
            if peak is None or current_mid > peak:
                ipr_state["peak_mid"] = current_mid
                peak = current_mid

            drawdown = peak - current_mid
            if drawdown > IPR_BAIL_DRAWDOWN:
                ipr_state["bail_count"] = ipr_state.get("bail_count", 0) + 1
            else:
                ipr_state["bail_count"] = 0

            if (ipr_state.get("mode") == "LINEAR"
                    and ipr_state["bail_count"] >= IPR_BAIL_CONSEC):
                ipr_state["mode"] = "SAFE"

        if ipr_state.get("mode") == "SAFE":
            return self.run_ipr_safe(depth, position, timestamp)
        return self.run_ipr_linear(depth, position, timestamp, ipr_state)

    # -------------------------------------------------------------------
    # IPR LINEAR MODE (primary: assumes upward drift at IPR_SLOPE)
    # -------------------------------------------------------------------

    def run_ipr_linear(self, depth, position, timestamp, ipr_state):
        # ---- 1. Fair value: initial price + slope * (ts - initial_ts) ----
        # On the very first tick we see, anchor fair to the current mid
        # and remember the timestamp. After that, we never re-read the
        # book for fair - we just advance it by IPR_SLOPE each timestamp.
        if "initial_fair" not in ipr_state:
            if not depth.buy_orders or not depth.sell_orders:
                return []
            mid0 = self._ipr_mid_l2_l3(depth)
            if math.isnan(mid0):
                best_bid = max(depth.buy_orders)
                best_ask = min(depth.sell_orders)
                mid0 = (best_bid + best_ask) / 2.0
            ipr_state["initial_fair"] = mid0
            ipr_state["initial_ts"]   = timestamp

        fair = ipr_state["initial_fair"] + IPR_SLOPE * (timestamp - ipr_state["initial_ts"])

        buy_capacity  = POSITION_LIMIT - position
        sell_capacity = POSITION_LIMIT + position
        orders = []

        # ---- 2. TAKE phase: aggressive buys (long_take_edge=-5), symmetric sells ----
        # Asks: buy any ask priced strictly less than fair - IPR_LONG_TAKE_EDGE.
        # With IPR_LONG_TAKE_EDGE = -5, we take asks up to fair + 5 (very aggressive).
        for ask_price in sorted(depth.sell_orders.keys()):
            if ask_price >= fair - IPR_LONG_TAKE_EDGE:
                break
            if buy_capacity <= 0:
                break
            available = -depth.sell_orders[ask_price]
            qty = min(available, buy_capacity)
            if qty > 0:
                orders.append(Order(IPR, ask_price, qty))
                buy_capacity -= qty

        # Bids: sell only on genuine mispricings (bid > fair + IPR_MIN_TAKE_EDGE)
        for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_price <= fair + IPR_MIN_TAKE_EDGE:
                break
            if sell_capacity <= 0:
                break
            available = depth.buy_orders[bid_price]
            qty = min(available, sell_capacity)
            if qty > 0:
                orders.append(Order(IPR, bid_price, -qty))
                sell_capacity -= qty

        # ---- 3. FLATTEN phase: 0-EV unwind if over softcap ----
        if abs(position) >= IPR_SOFT_CAP:
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
                        orders.append(Order(IPR, bid_price, -qty))
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
                        orders.append(Order(IPR, ask_price, qty))
                        buy_capacity -= qty
                        to_reduce -= qty

        # ---- 4. MAKE phase: post bid and ask (with long bias) ----
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        max_safe_bid = int(math.floor(fair)) - 1
        min_safe_ask = int(math.ceil(fair)) + 1

        # Start with beat-by-1
        if best_bid is not None:
            bid_price = best_bid + IPR_MAKE_BEAT_TICKS
        else:
            bid_price = max_safe_bid
        if bid_price > max_safe_bid:
            bid_price = max_safe_bid

        if best_ask is not None:
            ask_price = best_ask - IPR_MAKE_BEAT_TICKS
        else:
            ask_price = min_safe_ask
        if ask_price < min_safe_ask:
            ask_price = min_safe_ask

        # Apply long-bias: shift both quotes up by IPR_QUOTE_BIAS_TICKS
        bid_price += IPR_QUOTE_BIAS_TICKS
        ask_price += IPR_QUOTE_BIAS_TICKS

        # Safety re-clamp: bid can't be above fair, ask can't be below fair
        if IPR_BIAS_CLAMP:
            if bid_price > max_safe_bid:
                bid_price = max_safe_bid
            if ask_price < min_safe_ask:
                ask_price = min_safe_ask

        # Volume sizing (asymmetric: bid_frac=0.7, ask_frac=0.3)
        bid_volume = int(buy_capacity * IPR_MAKE_PORTION * IPR_BID_FRAC / 0.5)
        ask_volume = int(sell_capacity * IPR_MAKE_PORTION * IPR_ASK_FRAC / 0.5)
        bid_volume = min(bid_volume, buy_capacity)
        ask_volume = min(ask_volume, sell_capacity)

        # One-sided book: no L1 touch to beat — reduce size on fair-inferred leg.
        if best_bid is None:
            bid_volume = int(bid_volume * IPR_ONE_SIDED_VOLUME_FRAC)
        if best_ask is None:
            ask_volume = int(ask_volume * IPR_ONE_SIDED_VOLUME_FRAC)

        # Symmetric soft-cap pressure (same pattern as ACO): |pos| > softcap only.
        if abs(position) > IPR_SOFT_CAP:
            excess = (abs(position) - IPR_SOFT_CAP) / float(POSITION_LIMIT - IPR_SOFT_CAP)
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
            orders.append(Order(IPR, bid_price, bid_volume))
        if ask_volume > 0:
            orders.append(Order(IPR, ask_price, -ask_volume))

        return orders

    # -------------------------------------------------------------------
    # IPR SAFE MODE (fallback: plain symmetric market maker)
    # -------------------------------------------------------------------
    #
    # Activated if the bail trigger fires (mid has fallen materially from
    # its peak). Drops all drift assumptions. Fair = L2+L3 average (less
    # L1 noise than mid), else L1 mid. Symmetric everything, 50% make
    # volume, always flattens toward
    # zero. Goal is to unwind the long position we accumulated and keep
    # making small amounts of spread safely.
    # -------------------------------------------------------------------

    def run_ipr_safe(self, depth, position, timestamp):
        if not depth.buy_orders or not depth.sell_orders:
            return []

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        fair = self._ipr_mid_l2_l3(depth)
        if math.isnan(fair):
            fair = (best_bid + best_ask) / 2.0

        buy_capacity  = POSITION_LIMIT - position
        sell_capacity = POSITION_LIMIT + position
        orders = []

        # ---- 1. TAKE phase: symmetric, no long bias ----
        for ask_price in sorted(depth.sell_orders.keys()):
            if ask_price >= fair - IPR_SAFE_MIN_TAKE_EDGE:
                break
            if buy_capacity <= 0:
                break
            available = -depth.sell_orders[ask_price]
            qty = min(available, buy_capacity)
            if qty > 0:
                orders.append(Order(IPR, ask_price, qty))
                buy_capacity -= qty

        for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_price <= fair + IPR_SAFE_MIN_TAKE_EDGE:
                break
            if sell_capacity <= 0:
                break
            available = depth.buy_orders[bid_price]
            qty = min(available, sell_capacity)
            if qty > 0:
                orders.append(Order(IPR, bid_price, -qty))
                sell_capacity -= qty

        # ---- 2. FLATTEN phase: ALWAYS flatten toward zero at fair or better ----
        # Unlike LINEAR mode we do not gate on soft_cap - we want out.
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
                    orders.append(Order(IPR, bid_price, -qty))
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
                    orders.append(Order(IPR, ask_price, qty))
                    buy_capacity -= qty
                    to_reduce -= qty

        # ---- 3. MAKE phase: symmetric, wide quotes (back off from best) ----
        # We back OFF from the best by IPR_SAFE_MAKE_BEAT_TICKS ticks on
        # each side. With the default -2, our bid sits 2 ticks below best_bid
        # and our ask sits 2 ticks above best_ask - deep in the book. This
        # makes SAFE quotes "observer quotes": present but rarely filling,
        # so we don't accumulate wrong-side inventory if the bail false-fires.
        max_safe_bid = int(math.floor(fair)) - 1
        min_safe_ask = int(math.ceil(fair)) + 1

        bid_price = best_bid + IPR_SAFE_MAKE_BEAT_TICKS
        if bid_price > max_safe_bid:
            bid_price = max_safe_bid
        ask_price = best_ask - IPR_SAFE_MAKE_BEAT_TICKS
        if ask_price < min_safe_ask:
            ask_price = min_safe_ask

        # 50% of remaining capacity on each side (risk-off)
        bid_volume = int(buy_capacity * IPR_SAFE_MAKE_PORTION)
        ask_volume = int(sell_capacity * IPR_SAFE_MAKE_PORTION)
        bid_volume = min(bid_volume, buy_capacity)
        ask_volume = min(ask_volume, sell_capacity)

        # Symmetric inventory shrink when near the hard limit (pure safety)
        if abs(position) > 60:
            excess = (abs(position) - 60) / 20.0   # 60..80 -> 0..1
            if excess > 1.0:
                excess = 1.0
            if position > 0:
                bid_volume = int(bid_volume * (1.0 - excess))
            else:
                ask_volume = int(ask_volume * (1.0 - excess))

        if bid_volume > 0:
            orders.append(Order(IPR, bid_price, bid_volume))
        if ask_volume > 0:
            orders.append(Order(IPR, ask_price, -ask_volume))

        return orders

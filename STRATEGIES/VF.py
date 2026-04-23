"""
VF - combined ACO + IPR trader (final version).

ACO: hardcoded fair = 10,000 (primary). Falls back to a mid-of-best-bid/ask
fair if the market mid sits more than ACO_FALLBACK_DEVIATION ticks away
from the anchor for ACO_FALLBACK_CONSEC consecutive ticks. Latched.

IPR: linear-drift maker (primary). Falls back to a symmetric safe maker
if the mid drops IPR_BAIL_DRAWDOWN below its peak for IPR_BAIL_CONSEC
ticks. Latched.

Each product can be individually disabled via ACO_ENABLED / IPR_ENABLED.

The trading logic runs standalone: the only hard dependency is
`datamodel` (provided by the competition runtime). The tick-recording
logic is optional and fenced between BEGIN/END TICK RECORDING markers
so it can be stripped for submission by deleting those blocks (or just
left in - it self-disables if MODULES isn't importable).
"""

# =======================================================================
# IMPORTS
# =======================================================================

import json
import math

from datamodel import OrderDepth, TradingState, Order

# --- BEGIN TICK RECORDING (DELETE FOR COMPETITION SUBMISSION) ---
try:
    import _repo_path  # noqa: F401  # pyright: ignore[reportMissingImports]
    from MODULES import TickRecorder, logs_csv_path  # pyright: ignore[reportMissingImports]
except Exception:
    TickRecorder = None       # type: ignore
    logs_csv_path = None      # type: ignore
# --- END TICK RECORDING ---


# =======================================================================
# STRATEGY TOGGLES
# =======================================================================

ACO_ENABLED = True
IPR_ENABLED = True


# =======================================================================
# PRODUCT CONSTANTS
# =======================================================================

ACO = "ASH_COATED_OSMIUM"
IPR = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80


# =======================================================================
# ACO STRATEGY A  (primary: hardcoded fair = 10000)
# =======================================================================

ACO_ANCHOR          = 10000
ACO_SOFT_CAP        = 60
ACO_MAKE_PORTION    = 0.80
ACO_BID_FRAC        = 0.50
ACO_ASK_FRAC        = 0.50
ACO_MAKE_BEAT_TICKS = 1
ACO_MIN_TAKE_EDGE   = 1


def run_aco_primary(depth, position, aco_state):
    fair = float(ACO_ANCHOR)
    aco_state["fair"] = fair
    return _aco_pipeline(depth, position, fair)


# =======================================================================
# ACO STRATEGY B  (fallback: fair = mid of best bid and best ask)
# =======================================================================

def run_aco_fallback(depth, position, aco_state):
    best_bid = max(depth.buy_orders) if depth.buy_orders else 0
    best_ask = min(depth.sell_orders) if depth.sell_orders else 0
    fair_raw = (best_bid + best_ask) / 2.0

    if fair_raw != 0:
        fair = fair_raw
    else:
        fair = aco_state.get("fair", float(ACO_ANCHOR))

    aco_state["fair"] = fair
    return _aco_pipeline(depth, position, fair)


def _aco_pipeline(depth, position, fair):
    """Shared ACO TAKE / FLATTEN / MAKE pipeline. The only thing that
    differs between Strategy A and Strategy B is the `fair` value."""
    buy_cap, sell_cap = _capacity(position)
    orders = []

    new_orders, buy_cap = _take_below_fair(ACO, depth, fair, ACO_MIN_TAKE_EDGE, buy_cap)
    orders.extend(new_orders)

    new_orders, sell_cap = _take_above_fair(ACO, depth, fair, ACO_MIN_TAKE_EDGE, sell_cap)
    orders.extend(new_orders)

    if abs(position) >= ACO_SOFT_CAP:
        fair_int = int(round(fair))
        if position > 0:
            new_orders, sell_cap = _flatten_long(ACO, depth, fair_int, position, sell_cap)
            orders.extend(new_orders)
        elif position < 0:
            new_orders, buy_cap = _flatten_short(ACO, depth, fair_int, position, buy_cap)
            orders.extend(new_orders)

    make_orders = _aco_make(depth, position, fair, buy_cap, sell_cap)
    orders.extend(make_orders)

    return orders


def _aco_make(depth, position, fair, buy_cap, sell_cap):
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

    bid_volume = int(buy_cap * ACO_MAKE_PORTION * ACO_BID_FRAC / 0.5)
    ask_volume = int(sell_cap * ACO_MAKE_PORTION * ACO_ASK_FRAC / 0.5)
    bid_volume = min(bid_volume, buy_cap)
    ask_volume = min(ask_volume, sell_cap)

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

    orders = []
    if bid_volume > 0:
        orders.append(Order(ACO, bid_price, bid_volume))
    if ask_volume > 0:
        orders.append(Order(ACO, ask_price, -ask_volume))
    return orders


# =======================================================================
# ACO SWITCHING LOGIC  (30 ticks away from anchor for 15 consecutive ticks)
# =======================================================================

ACO_FALLBACK_DEVIATION = 30
ACO_FALLBACK_CONSEC    = 15


def update_aco_mode(depth, aco_state):
    """Look at the market mid and latch into FALLBACK mode if it has
    drifted too far from ACO_ANCHOR for too long. Once in FALLBACK, stay."""
    if aco_state.get("mode") == "FALLBACK":
        return

    if not depth.buy_orders or not depth.sell_orders:
        return

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2.0

    if abs(mid - ACO_ANCHOR) > ACO_FALLBACK_DEVIATION:
        aco_state["trip_count"] = aco_state.get("trip_count", 0) + 1
    else:
        aco_state["trip_count"] = 0

    if aco_state["trip_count"] >= ACO_FALLBACK_CONSEC:
        aco_state["mode"] = "FALLBACK"


# =======================================================================
# IPR STRATEGY A  (LINEAR drift maker: long-biased)
# =======================================================================

IPR_SOFT_CAP              = 70
IPR_MAKE_PORTION          = 0.90
IPR_BID_FRAC              = 0.70
IPR_ASK_FRAC              = 0.30
IPR_MAKE_BEAT_TICKS       = 1
IPR_MIN_TAKE_EDGE         = 1
IPR_LONG_TAKE_EDGE        = -5
IPR_SLOPE                 = 0.0012
IPR_QUOTE_BIAS_TICKS      = 2
IPR_BIAS_CLAMP            = True
IPR_ONE_SIDED_VOLUME_FRAC = 0.5


def run_ipr_linear(depth, position, timestamp, ipr_state):
    if "initial_fair" not in ipr_state:
        if not depth.buy_orders or not depth.sell_orders:
            return []
        ipr_state["initial_fair"] = _ipr_book_mid(depth)
        ipr_state["initial_ts"]   = timestamp

    fair = ipr_state["initial_fair"] + IPR_SLOPE * (timestamp - ipr_state["initial_ts"])
    ipr_state["fair"] = float(fair)

    buy_cap, sell_cap = _capacity(position)
    orders = []

    new_orders, buy_cap = _take_below_fair(IPR, depth, fair, IPR_LONG_TAKE_EDGE, buy_cap)
    orders.extend(new_orders)

    new_orders, sell_cap = _take_above_fair(IPR, depth, fair, IPR_MIN_TAKE_EDGE, sell_cap)
    orders.extend(new_orders)

    if abs(position) >= IPR_SOFT_CAP:
        fair_int = int(round(fair))
        if position > 0:
            new_orders, sell_cap = _flatten_long(IPR, depth, fair_int, position, sell_cap)
            orders.extend(new_orders)
        elif position < 0:
            new_orders, buy_cap = _flatten_short(IPR, depth, fair_int, position, buy_cap)
            orders.extend(new_orders)

    make_orders = _ipr_linear_make(depth, position, fair, buy_cap, sell_cap)
    orders.extend(make_orders)

    return orders


def _ipr_linear_make(depth, position, fair, buy_cap, sell_cap):
    best_bid = max(depth.buy_orders) if depth.buy_orders else None
    best_ask = min(depth.sell_orders) if depth.sell_orders else None

    max_safe_bid = int(math.floor(fair)) - 1
    min_safe_ask = int(math.ceil(fair)) + 1

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

    # Long-bias: shift both quotes up, then clamp so bid stays below fair.
    bid_price += IPR_QUOTE_BIAS_TICKS
    ask_price += IPR_QUOTE_BIAS_TICKS
    if IPR_BIAS_CLAMP:
        if bid_price > max_safe_bid:
            bid_price = max_safe_bid
        if ask_price < min_safe_ask:
            ask_price = min_safe_ask

    bid_volume = int(buy_cap * IPR_MAKE_PORTION * IPR_BID_FRAC / 0.5)
    ask_volume = int(sell_cap * IPR_MAKE_PORTION * IPR_ASK_FRAC / 0.5)
    bid_volume = min(bid_volume, buy_cap)
    ask_volume = min(ask_volume, sell_cap)

    # No L1 touch on a side -> shrink the quote we inferred from fair.
    if best_bid is None:
        bid_volume = int(bid_volume * IPR_ONE_SIDED_VOLUME_FRAC)
    if best_ask is None:
        ask_volume = int(ask_volume * IPR_ONE_SIDED_VOLUME_FRAC)

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

    orders = []
    if bid_volume > 0:
        orders.append(Order(IPR, bid_price, bid_volume))
    if ask_volume > 0:
        orders.append(Order(IPR, ask_price, -ask_volume))
    return orders


# =======================================================================
# IPR STRATEGY B  (SAFE symmetric maker: no drift assumption)
# =======================================================================

IPR_SAFE_MAKE_PORTION    = 0.50
IPR_SAFE_MIN_TAKE_EDGE   = 1
IPR_SAFE_MAKE_BEAT_TICKS = -2   # back OFF: bid sits 2 below best_bid, etc.


def run_ipr_safe(depth, position, ipr_state):
    if not depth.buy_orders or not depth.sell_orders:
        return []

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    fair = _ipr_book_mid(depth)
    ipr_state["fair"] = float(fair)

    buy_cap, sell_cap = _capacity(position)
    orders = []

    new_orders, buy_cap = _take_below_fair(IPR, depth, fair, IPR_SAFE_MIN_TAKE_EDGE, buy_cap)
    orders.extend(new_orders)

    new_orders, sell_cap = _take_above_fair(IPR, depth, fair, IPR_SAFE_MIN_TAKE_EDGE, sell_cap)
    orders.extend(new_orders)

    # SAFE mode always flattens toward zero, not gated on soft cap.
    fair_int = int(round(fair))
    if position > 0:
        new_orders, sell_cap = _flatten_long(IPR, depth, fair_int, position, sell_cap)
        orders.extend(new_orders)
    elif position < 0:
        new_orders, buy_cap = _flatten_short(IPR, depth, fair_int, position, buy_cap)
        orders.extend(new_orders)

    max_safe_bid = int(math.floor(fair)) - 1
    min_safe_ask = int(math.ceil(fair)) + 1

    bid_price = best_bid + IPR_SAFE_MAKE_BEAT_TICKS
    if bid_price > max_safe_bid:
        bid_price = max_safe_bid
    ask_price = best_ask - IPR_SAFE_MAKE_BEAT_TICKS
    if ask_price < min_safe_ask:
        ask_price = min_safe_ask

    bid_volume = int(buy_cap * IPR_SAFE_MAKE_PORTION)
    ask_volume = int(sell_cap * IPR_SAFE_MAKE_PORTION)
    bid_volume = min(bid_volume, buy_cap)
    ask_volume = min(ask_volume, sell_cap)

    # Shrink the wrong-side quote when we are near the hard limit.
    if abs(position) > 60:
        excess = (abs(position) - 60) / 20.0
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


# =======================================================================
# IPR SWITCHING LOGIC  (peak-mid drawdown bail)
# =======================================================================

IPR_BAIL_DRAWDOWN = 30
IPR_BAIL_CONSEC   = 5


def update_ipr_mode(depth, ipr_state):
    if ipr_state.get("mode") == "SAFE":
        return
    if not depth.buy_orders or not depth.sell_orders:
        return

    current_mid = _ipr_book_mid(depth)

    peak = ipr_state.get("peak_mid")
    if peak is None or current_mid > peak:
        ipr_state["peak_mid"] = current_mid
        peak = current_mid

    drawdown = peak - current_mid
    if drawdown > IPR_BAIL_DRAWDOWN:
        ipr_state["bail_count"] = ipr_state.get("bail_count", 0) + 1
    else:
        ipr_state["bail_count"] = 0

    if ipr_state["bail_count"] >= IPR_BAIL_CONSEC:
        ipr_state["mode"] = "SAFE"


# =======================================================================
# SHARED HELPERS
# =======================================================================

def _capacity(position):
    """Return (buy_capacity, sell_capacity) given the current position."""
    return POSITION_LIMIT - position, POSITION_LIMIT + position


def _take_below_fair(symbol, depth, fair, min_edge, buy_cap):
    """Buy asks priced strictly below fair - min_edge, up to buy_cap."""
    orders = []
    for ask_price in sorted(depth.sell_orders.keys()):
        if ask_price >= fair - min_edge:
            break
        if buy_cap <= 0:
            break
        available = -depth.sell_orders[ask_price]
        qty = min(available, buy_cap)
        if qty > 0:
            orders.append(Order(symbol, ask_price, qty))
            buy_cap -= qty
    return orders, buy_cap


def _take_above_fair(symbol, depth, fair, min_edge, sell_cap):
    """Sell into bids priced strictly above fair + min_edge, up to sell_cap."""
    orders = []
    for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
        if bid_price <= fair + min_edge:
            break
        if sell_cap <= 0:
            break
        available = depth.buy_orders[bid_price]
        qty = min(available, sell_cap)
        if qty > 0:
            orders.append(Order(symbol, bid_price, -qty))
            sell_cap -= qty
    return orders, sell_cap


def _flatten_long(symbol, depth, fair_int, position, sell_cap):
    """Sell down a long position at any bid >= fair (0-EV unwind)."""
    orders = []
    to_reduce = min(position, sell_cap)
    for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
        if to_reduce <= 0:
            break
        if bid_price < fair_int:
            break
        available = depth.buy_orders[bid_price]
        qty = min(available, to_reduce)
        if qty > 0:
            orders.append(Order(symbol, bid_price, -qty))
            sell_cap -= qty
            to_reduce -= qty
    return orders, sell_cap


def _flatten_short(symbol, depth, fair_int, position, buy_cap):
    """Buy back a short position at any ask <= fair (0-EV unwind)."""
    orders = []
    to_reduce = min(-position, buy_cap)
    for ask_price in sorted(depth.sell_orders.keys()):
        if to_reduce <= 0:
            break
        if ask_price > fair_int:
            break
        available = -depth.sell_orders[ask_price]
        qty = min(available, to_reduce)
        if qty > 0:
            orders.append(Order(symbol, ask_price, qty))
            buy_cap -= qty
            to_reduce -= qty
    return orders, buy_cap


def _ipr_book_mid(depth):
    """Average of L2+L3 bid prices and L2+L3 ask prices, with shallow-book
    fallbacks: use whatever levels exist, and ultimately L1 mid."""
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
    mid = sum(all_levels) / len(all_levels)

    if math.isnan(mid):
        mid = (bids[0] + asks[0]) / 2.0
    return mid


# =======================================================================
# TRADER
# =======================================================================

class Trader:
    # --- BEGIN TICK RECORDING (DELETE FOR COMPETITION SUBMISSION) ---
    def __init__(self, tick_recorder=None, record_ticks=True, sandbox_stdout=None):
        if tick_recorder is not None:
            self.tick_recorder = tick_recorder
        elif record_ticks and TickRecorder is not None:
            self.tick_recorder = TickRecorder(auto_save_csv=logs_csv_path("VF_ticks"))
        else:
            self.tick_recorder = None

        if sandbox_stdout is None:
            sandbox_stdout = record_ticks
        self._sandbox_stdout = sandbox_stdout
    # --- END TICK RECORDING ---

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

            if product == ACO and ACO_ENABLED:
                result[product] = self._dispatch_aco(depth, position, saved)
            elif product == IPR and IPR_ENABLED:
                result[product] = self._dispatch_ipr(depth, position, state.timestamp, saved)
            else:
                result[product] = []

        trader_data_out = json.dumps(saved)

        # --- BEGIN TICK RECORDING (DELETE FOR COMPETITION SUBMISSION) ---
        if self.tick_recorder is not None:
            fair_out = {}
            aco_fair = saved.get("aco", {}).get("fair")
            if aco_fair is not None:
                fair_out[ACO] = float(aco_fair)
            ipr_fair = saved.get("ipr", {}).get("fair")
            if ipr_fair is not None:
                fair_out[IPR] = float(ipr_fair)
            self.tick_recorder.record_and_emit(
                state, result,
                fair=fair_out or None,
                sandbox_stdout=self._sandbox_stdout,
            )
        # --- END TICK RECORDING ---

        return result, 0, trader_data_out

    def _dispatch_aco(self, depth: OrderDepth, position: int, saved):
        aco_state = saved.setdefault("aco", {"mode": "PRIMARY"})
        update_aco_mode(depth, aco_state)
        if aco_state.get("mode") == "FALLBACK":
            return run_aco_fallback(depth, position, aco_state)
        return run_aco_primary(depth, position, aco_state)

    def _dispatch_ipr(self, depth: OrderDepth, position: int, timestamp: int, saved):
        ipr_state = saved.setdefault("ipr", {"mode": "LINEAR"})
        update_ipr_mode(depth, ipr_state)
        if ipr_state.get("mode") == "SAFE":
            return run_ipr_safe(depth, position, ipr_state)
        return run_ipr_linear(depth, position, timestamp, ipr_state)

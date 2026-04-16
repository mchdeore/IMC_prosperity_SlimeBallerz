"""
Primo v1 - Prosperity 4 Round 1 strategy
========================================

This file trades two products:

    1. ASH_COATED_OSMIUM (ACO)  - a noisy, mean-reverting product.
    2. INTARIAN_PEPPER_ROOT (IPR) - a product whose price drifts upward at
       a very steady rate of about 0.001 price units per timestamp step.

We use THREE strategies, one per product (plus a fallback for IPR):

    Strategy 1  (ACO)          -> `_run_aco`
    Strategy 2  (IPR primary)  -> `_run_ipr_linear`       (assumes drift holds)
    Strategy 3  (IPR fallback) -> `_run_ipr_momentum`     (used if drift breaks)

All three strategies share the same three "phases" of order placement:

    Phase A - TAKE:     aggressively hit the book when the price is wrong
                        (ask is below fair, or bid is above fair) by at
                        least `min_take_edge` ticks.

    Phase B - FLATTEN:  if our position has gotten too big (magnitude
                        above `soft_cap`), take ANY trade at fair or
                        better to push the position back toward zero.
                        These are zero-expected-value trades - we only
                        use them to reduce risk, never to make money.

    Phase C - MAKE:     post a bid and an ask on the book to capture
                        spread. We "beat by 1 tick" by default.

All knobs live in the `ACO_CFG`, `IPR_A_CFG`, `IPR_B_CFG` dicts at the
top of the file. Change a number there, rerun the backtest, done.
"""

from datamodel import OrderDepth, TradingState, Order
import json
import math


# -----------------------------------------------------------------------
# Product name constants
# -----------------------------------------------------------------------
ACO = "ASH_COATED_OSMIUM"
IPR = "INTARIAN_PEPPER_ROOT"


# =======================================================================
# CONFIG BLOCKS
# =======================================================================
# Everything in these dicts can be tuned without touching the trading
# logic below. Each setting has an inline comment describing what it
# does.
# =======================================================================

ACO_CFG = {
    # --- Position sizing ---
    "position_limit":     80,     # hard cap: the exchange won't let us go past this
    "soft_cap":           75,     # once |position| >= this, we start flattening and tightening

    # --- Maker volumes ---
    "make_portion":       0.80,   # we post 80% of remaining capacity as maker volume
    "bid_frac":           0.50,   # of that 80%, how much goes on the bid side
    "ask_frac":           0.50,   # and how much on the ask side (should sum to 1.0)

    # --- Maker placement ---
    "make_beat_ticks":    1,      # bid = best_bid + 1, ask = best_ask - 1 (beat by N)
    "quote_bias_ticks":   0,      # shift BOTH bid and ask up by N ticks (+ = lean long)
    "bias_clamp_to_fair": True,   # if True, bid can't be pushed above fair by bias (safe mode)

    # --- Taker edge ---
    "min_take_edge":      1,      # only hit if we make at least this many ticks vs fair

    # --- Fair value ---
    "fair_levels":        [1, 2, 3], # book levels averaged to compute fair (per side)
    "ema_alpha_new":      0.10,   # fair = 0.10 * new_observation + 0.90 * previous_fair (slow EMA)
    "anchor":             10_000, # fallback price if the book is totally empty

    # --- Inventory management ---
    "flatten_at_fair":    True,   # if True, Phase B fires when |pos| >= soft_cap
    "pressure_mode":      "symmetric",  # "symmetric" | "long_bias" | "off"
}


IPR_A_CFG = {
    # --- Position sizing ---
    "position_limit":     80,
    "soft_cap":           75,

    # --- Maker volumes ---
    "make_portion":       0.90,
    "bid_frac":           0.70,   # default: more volume on the bid side (we want to go long)
    "ask_frac":           0.30,

    # --- Maker placement ---
    "make_beat_ticks":    1,
    "quote_bias_ticks":   3,      # set to +1 or +2 to explicitly lean long
    "bias_clamp_to_fair": True,   # start True (safe). Flip to False after live confirms drift.

    # --- Taker edge ---
    "min_take_edge":      1,

    # --- Fair value ---
    "slope":              0.001,  # drift per raw timestamp unit (best-fit across 3 training days)

    # --- Inventory management ---
    "flatten_at_fair":    True,
    "pressure_mode":      "long_bias",  # only tighten when SHORT; let the drift carry us long

    # --- Bail trigger: if fair value is clearly wrong, switch to IPR-B ---
    "bail_dev_threshold": 13,     # |best_quote - fair| ticks. above this counts as "too far"
    "bail_consecutive":   5,      # we need 5 ticks in a row over the threshold to bail
    "bail_latch":         True,   # once we've switched to B, never switch back to A
}


IPR_B_CFG = {
    # --- Position sizing ---
    "position_limit":     80,
    "soft_cap":           75,

    # --- Maker volumes ---
    "make_portion":       0.90,
    "bid_frac":           0.50,
    "ask_frac":           0.50,

    # --- Maker placement ---
    "make_beat_ticks":    1,
    "quote_bias_ticks":   0,      # usually left at 0; momentum skew below drives the bias instead
    "bias_clamp_to_fair": False,

    # --- Taker edge ---
    "min_take_edge":      1,

    # --- Fair value ---
    "fair_levels":        [2, 3], # same book-level choice as ACO
    "roc_window":         20,     # number of past ticks used to estimate rate of change

    # --- Inventory management ---
    "flatten_at_fair":    True,
    "pressure_mode":      "symmetric",

    # --- Momentum-based quote skew ---
    "max_skew_ticks":     3,      # cap on how many ticks momentum can shift our quotes
    "skew_per_roc_unit":  1000,   # roc * this = raw skew ticks (before the cap). tune empirically.
}


GLOBAL = {
    "active":  "BOTH",    # "ACO" | "IPR" | "BOTH" - lets you turn one product off
    "verbose": False,     # if True, print a debug line every tick
}


# =======================================================================
# TRADER CLASS
# =======================================================================

class Trader:

    # -------------------------------------------------------------------
    # State serialization helpers
    #
    # The Prosperity engine gives us a string `traderData` at the start
    # of every tick and takes one back at the end. We store a small JSON
    # dict in there so we can remember things like the previous fair
    # value, the IPR initial price, etc.
    # -------------------------------------------------------------------

    @staticmethod
    def _load_state(trader_data_str):
        """Turn the engine's string back into our state dict."""
        if not trader_data_str:
            return {}
        try:
            return json.loads(trader_data_str)
        except Exception:
            return {}

    @staticmethod
    def _save_state(state_dict):
        """Turn our state dict into a string for the engine to store."""
        return json.dumps(state_dict)

    def _log(self, message):
        """Print only if GLOBAL['verbose'] is True."""
        if GLOBAL["verbose"]:
            print(message)

    # -------------------------------------------------------------------
    # MAIN ENTRY POINT
    # -------------------------------------------------------------------

    def run(self, state: TradingState):
        """Called by the engine once per timestamp. Returns orders."""
        saved_state = self._load_state(state.traderData)
        result = {}

        # Decide which products we're allowed to trade this run
        active = GLOBAL["active"]
        trade_aco = (active in ("ACO", "BOTH"))
        trade_ipr = (active in ("IPR", "BOTH"))

        for product in state.order_depths:
            depth = state.order_depths[product]
            position = state.position.get(product, 0)

            if product == ACO and trade_aco:
                orders = self._run_aco(depth, position, state.timestamp, saved_state)
                result[product] = orders

            elif product == IPR and trade_ipr:
                orders = self._run_ipr(depth, position, state.timestamp, saved_state)
                result[product] = orders

            else:
                result[product] = []

        trader_data_out = self._save_state(saved_state)
        return result, 0, trader_data_out

    # ===================================================================
    # STRATEGY 1: ASH_COATED_OSMIUM (ACO)
    # ===================================================================
    #
    # What we assume:
    #     ACO is noisy but mean-reverting around ~10,000. The top of the
    #     book (L1) is often just one lonely quote with a big gap behind
    #     it, so we use L2 and L3 (deeper levels) to compute a more
    #     stable fair value.
    #
    # Fair value:
    #     bid_mid = average of the prices at bid_L1, bid_L2, bid_L3
    #     ask_mid = average of the prices at ask_L1, ask_L2, ask_L3
    #     book_fair = (bid_mid + ask_mid) / 2
    #     fair = 0.10 * book_fair + 0.90 * previous_fair     (slow EMA)
    #     Missing levels are skipped (e.g. if only L1 and L2 are
    #     present, we average just those two). Only if a whole side is
    #     empty do we fall back to the previous fair.
    #
    # Inventory management:
    #     Symmetric pressure. When |position| goes past `soft_cap`, we
    #     both (a) start hitting the book for 0-EV trades to reduce
    #     position, and (b) pull our maker quotes toward fair on the
    #     corresponding side, while haircutting volume on that side.
    # ===================================================================

    def _run_aco(self, depth, position, timestamp, saved_state):
        """Run Strategy 1 for ACO. Returns a list of Order objects."""
        cfg = ACO_CFG
        aco_state = saved_state.setdefault("aco", {})

        # ---- Fair value (step-by-step) ----
        previous_fair = aco_state.get("fair")
        book_fair = self._aco_book_fair(depth, cfg["fair_levels"])
        fair = self._aco_update_fair(book_fair, previous_fair, cfg)
        aco_state["fair"] = fair

        # ---- Capacity remaining this tick ----
        buy_capacity = cfg["position_limit"] - position
        sell_capacity = cfg["position_limit"] + position

        all_orders = []

        # ---- Phase A: aggressive take ----
        take_orders, buy_capacity, sell_capacity = self._phase_take(
            ACO, depth, fair, buy_capacity, sell_capacity,
            min_take_edge=cfg["min_take_edge"],
        )
        all_orders.extend(take_orders)

        # ---- Phase B: 0-EV flatten, only when over softcap ----
        if cfg["flatten_at_fair"] and abs(position) >= cfg["soft_cap"]:
            flatten_orders, buy_capacity, sell_capacity = self._phase_flatten(
                ACO, depth, fair, position, buy_capacity, sell_capacity,
            )
            all_orders.extend(flatten_orders)

        # ---- Phase C: make ----
        make_orders = self._phase_make(
            ACO, depth, fair, position, buy_capacity, sell_capacity, cfg,
        )
        all_orders.extend(make_orders)

        self._log("[ACO] t={} pos={:+d} fair={:.1f} orders={}".format(
            timestamp, position, fair, len(all_orders),
        ))
        return all_orders

    @staticmethod
    def _aco_book_fair(depth, levels):
        """
        Compute a fair value from the book using the specified levels
        (e.g. [1, 2, 3] means "average L1, L2, and L3 on each side").

        Missing-level handling:
            - If a requested level does not exist on a side (for example,
              the book only has 2 bid levels when we asked for 3), we
              skip that level and average only the prices that ARE
              present on that side.
            - If a whole side (all bids OR all asks) is empty, we return
              None. The caller (`_aco_update_fair`) will then fall back
              to the previous cycle's fair value.

        Returns a float, or None if either side is completely empty.
        """
        # Sort bids highest-to-lowest and asks lowest-to-highest, so
        # levels[0] = L1, levels[1] = L2, etc.
        sorted_bids = sorted(depth.buy_orders.keys(), reverse=True)
        sorted_asks = sorted(depth.sell_orders.keys())

        # Either side totally empty -> give up, caller will use prev fair
        if not sorted_bids or not sorted_asks:
            return None

        # Collect only the prices that actually exist at the requested
        # levels. Missing levels are silently skipped.
        bid_prices = []
        for level in levels:
            index = level - 1   # level 1 is index 0
            if index < len(sorted_bids):
                bid_prices.append(sorted_bids[index])

        ask_prices = []
        for level in levels:
            index = level - 1
            if index < len(sorted_asks):
                ask_prices.append(sorted_asks[index])

        # Defensive: if somehow we ended up with zero prices on either
        # side (shouldn't happen given the early-exit above), bail out.
        if not bid_prices or not ask_prices:
            return None

        bid_mid = sum(bid_prices) / len(bid_prices)
        ask_mid = sum(ask_prices) / len(ask_prices)
        return (bid_mid + ask_mid) / 2.0

    @staticmethod
    def _aco_update_fair(book_fair, previous_fair, cfg):
        """Blend the new book-fair with the previous fair via slow EMA."""
        alpha = cfg["ema_alpha_new"]   # weight on the NEW observation

        # Case 1: we have a fresh book reading
        if book_fair is not None:
            if previous_fair is None:
                return book_fair
            return alpha * book_fair + (1.0 - alpha) * previous_fair

        # Case 2: no book reading -> keep using the previous fair
        if previous_fair is not None:
            return previous_fair

        # Case 3: no history at all -> fall back to anchor
        return float(cfg["anchor"])

    # ===================================================================
    # IPR DISPATCH (Strategy 2 or 3)
    # ===================================================================

    def _run_ipr(self, depth, position, timestamp, saved_state):
        """
        Pick between IPR-A (linear drift) and IPR-B (momentum fallback)
        based on persisted mode. IPR-A will flip us to B if its bail
        trigger fires.
        """
        ipr_state = saved_state.setdefault("ipr", {"mode": "A"})
        mode = ipr_state.get("mode", "A")

        if mode == "A":
            return self._run_ipr_linear(depth, position, timestamp, ipr_state)
        else:
            return self._run_ipr_momentum(depth, position, timestamp, ipr_state)

    # ===================================================================
    # STRATEGY 2: IPR-A - linear drift maker (primary)
    # ===================================================================
    #
    # What we assume:
    #     IPR's price is well modeled by:
    #         fair(t) = initial_price + 0.001 * (t - initial_timestamp)
    #     We saw drift of ~0.001 per raw timestamp unit across 3 training
    #     days (very consistent).
    #
    # Fair value:
    #     On the FIRST tick we see, we take (best_bid + best_ask) / 2
    #     and save it as `initial_fair` along with `initial_ts`. On
    #     every subsequent tick, fair = initial_fair + slope * dt.
    #     We never re-read the book for fair after init.
    #
    # Inventory management:
    #     "long_bias" asymmetric pressure. If we're short past
    #     `soft_cap` we tighten the bid and shrink sell volume. If
    #     we're long past `soft_cap` we DO NOTHING - the drift is
    #     expected to bail us out. Optional `quote_bias_ticks` adds
    #     a proactive long lean (see docstring at top of _phase_make).
    #
    # Bail trigger:
    #     We track the last 5 values of |nearest_best_quote - fair|.
    #     If all 5 exceed `bail_dev_threshold`, we flip mode to "B".
    # ===================================================================

    def _run_ipr_linear(self, depth, position, timestamp, ipr_state):
        """Run Strategy 2. Mutates ipr_state (may switch mode to 'B')."""
        cfg = IPR_A_CFG

        # ---- Fair value ----
        # Initialize on the first tick we see.
        if "initial_fair" not in ipr_state:
            mid = self._simple_mid(depth)
            if mid is None:
                # Can't initialize yet - skip this tick.
                return []
            ipr_state["initial_fair"] = mid
            ipr_state["initial_ts"] = timestamp

        initial_fair = ipr_state["initial_fair"]
        initial_ts = ipr_state["initial_ts"]
        fair = initial_fair + cfg["slope"] * (timestamp - initial_ts)

        # ---- Bail check (might flip us to Strategy B) ----
        self._ipr_a_check_bail(depth, fair, ipr_state, cfg)
        if ipr_state.get("mode") == "B":
            # We just flipped; let IPR-B handle this tick immediately.
            return self._run_ipr_momentum(depth, position, timestamp, ipr_state)

        # ---- Capacity and phases ----
        buy_capacity = cfg["position_limit"] - position
        sell_capacity = cfg["position_limit"] + position

        all_orders = []

        take_orders, buy_capacity, sell_capacity = self._phase_take(
            IPR, depth, fair, buy_capacity, sell_capacity,
            min_take_edge=cfg["min_take_edge"],
        )
        all_orders.extend(take_orders)

        if cfg["flatten_at_fair"] and abs(position) >= cfg["soft_cap"]:
            flatten_orders, buy_capacity, sell_capacity = self._phase_flatten(
                IPR, depth, fair, position, buy_capacity, sell_capacity,
            )
            all_orders.extend(flatten_orders)

        make_orders = self._phase_make(
            IPR, depth, fair, position, buy_capacity, sell_capacity, cfg,
        )
        all_orders.extend(make_orders)

        self._log("[IPR-A] t={} pos={:+d} fair={:.1f} orders={}".format(
            timestamp, position, fair, len(all_orders),
        ))
        return all_orders

    def _ipr_a_check_bail(self, depth, fair, ipr_state, cfg):
        """
        Track the last N values of |best_quote_closest_to_fair - fair|.
        If all of the last `bail_consecutive` values exceed
        `bail_dev_threshold`, flip ipr_state['mode'] to 'B'.
        """
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        if best_bid is None or best_ask is None:
            return   # incomplete book, skip this check

        # Whichever side is CLOSER to fair is the one we use for the check.
        bid_distance = abs(best_bid - fair)
        ask_distance = abs(best_ask - fair)
        closest_distance = min(bid_distance, ask_distance)

        history = ipr_state.setdefault("dev_history", [])
        history.append(closest_distance)

        # Keep only the last N entries
        keep_n = cfg["bail_consecutive"]
        if len(history) > keep_n:
            del history[:-keep_n]

        # If we have enough samples AND all of them exceed the threshold,
        # flip to Strategy B.
        if len(history) >= keep_n:
            all_exceed = all(d > cfg["bail_dev_threshold"] for d in history)
            if all_exceed:
                ipr_state["mode"] = "B"
                self._log("[IPR-A] BAIL -> switching to Strategy B "
                          "(devs={}, threshold={})".format(
                              history, cfg["bail_dev_threshold"]))

    # ===================================================================
    # STRATEGY 3: IPR-B - momentum maker (fallback)
    # ===================================================================
    #
    # What we assume:
    #     The linear drift model has broken. We now look at the recent
    #     rate of change (ROC) of the book mid-price and quote toward
    #     that direction.
    #
    # Fair value:
    #     Every tick, compute book_fair = average of L2/L3 mids (same
    #     recipe as ACO). If any of the 4 values is missing, estimate
    #     with previous_fair + roc * dt instead. Keep a rolling history
    #     of the last `roc_window` fairs to compute roc.
    #
    # Inventory management:
    #     Symmetric scaled pressure (same as ACO) PLUS a momentum-driven
    #     quote skew: we shift BOTH bid and ask in the direction of
    #     momentum by `round(roc * skew_per_roc_unit)` ticks, capped
    #     by `max_skew_ticks`.
    # ===================================================================

    def _run_ipr_momentum(self, depth, position, timestamp, ipr_state):
        """Run Strategy 3. Maintains its own rolling ROC history."""
        cfg = IPR_B_CFG

        fair_history = ipr_state.setdefault("fair_history", [])
        ts_history = ipr_state.setdefault("ts_history", [])
        previous_fair = fair_history[-1] if fair_history else None
        previous_ts = ts_history[-1] if ts_history else None

        # ---- Rate of change over the rolling window ----
        roc = self._ipr_b_compute_roc(fair_history, ts_history, cfg["roc_window"])

        # ---- Fair value ----
        book_fair = self._aco_book_fair(depth, cfg["fair_levels"])
        if book_fair is not None:
            fair = book_fair
        elif previous_fair is not None and previous_ts is not None:
            # Book is missing one of the levels - extrapolate from ROC.
            fair = previous_fair + roc * (timestamp - previous_ts)
        else:
            # No history at all - fall back to the simple mid.
            simple = self._simple_mid(depth)
            fair = simple if simple is not None else 10_000.0

        # Update history
        fair_history.append(fair)
        ts_history.append(timestamp)
        if len(fair_history) > cfg["roc_window"] + 1:
            del fair_history[:1]
            del ts_history[:1]

        # ---- Momentum-based extra quote bias ----
        # Translate roc into a ticks-of-skew number, then clip.
        raw_skew = int(round(roc * cfg["skew_per_roc_unit"]))
        if raw_skew > cfg["max_skew_ticks"]:
            momentum_skew = cfg["max_skew_ticks"]
        elif raw_skew < -cfg["max_skew_ticks"]:
            momentum_skew = -cfg["max_skew_ticks"]
        else:
            momentum_skew = raw_skew

        # We add momentum_skew on top of whatever quote_bias_ticks says.
        # Instead of mutating the config dict (dangerous), we build a
        # one-tick-only copy that _phase_make will use.
        effective_cfg = dict(cfg)
        effective_cfg["quote_bias_ticks"] = cfg["quote_bias_ticks"] + momentum_skew

        # ---- Capacity and phases ----
        buy_capacity = cfg["position_limit"] - position
        sell_capacity = cfg["position_limit"] + position
        all_orders = []

        take_orders, buy_capacity, sell_capacity = self._phase_take(
            IPR, depth, fair, buy_capacity, sell_capacity,
            min_take_edge=cfg["min_take_edge"],
        )
        all_orders.extend(take_orders)

        if cfg["flatten_at_fair"] and abs(position) >= cfg["soft_cap"]:
            flatten_orders, buy_capacity, sell_capacity = self._phase_flatten(
                IPR, depth, fair, position, buy_capacity, sell_capacity,
            )
            all_orders.extend(flatten_orders)

        make_orders = self._phase_make(
            IPR, depth, fair, position, buy_capacity, sell_capacity, effective_cfg,
        )
        all_orders.extend(make_orders)

        self._log("[IPR-B] t={} pos={:+d} fair={:.1f} roc={:+.4f} skew={:+d} orders={}".format(
            timestamp, position, fair, roc, momentum_skew, len(all_orders),
        ))
        return all_orders

    @staticmethod
    def _ipr_b_compute_roc(fair_history, ts_history, window):
        """
        Estimate rate of change as (newest_fair - oldest_fair) divided
        by (newest_ts - oldest_ts). Returns 0.0 if we don't have enough
        history yet.
        """
        if len(fair_history) < 2 or len(ts_history) < 2:
            return 0.0
        # Use up to `window` most recent samples
        look_back = min(window, len(fair_history))
        oldest_fair = fair_history[-look_back]
        oldest_ts = ts_history[-look_back]
        newest_fair = fair_history[-1]
        newest_ts = ts_history[-1]
        dt = newest_ts - oldest_ts
        if dt <= 0:
            return 0.0
        return (newest_fair - oldest_fair) / dt

    @staticmethod
    def _simple_mid(depth):
        """Average of best bid and best ask, or None if the book is empty."""
        if not depth.buy_orders or not depth.sell_orders:
            return None
        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        return (best_bid + best_ask) / 2.0

    # ===================================================================
    # SHARED PHASES (used by all three strategies)
    # ===================================================================

    @staticmethod
    def _phase_take(product, depth, fair, buy_capacity, sell_capacity,
                    min_take_edge):
        """
        Phase A: aggressive taker.

        Buy any ask priced strictly less than `fair - min_take_edge`.
        Sell into any bid priced strictly greater than `fair + min_take_edge`.
        Walk from best price inward - stop when the edge runs out.
        """
        orders = []

        # ---- Buy from asks that are too cheap ----
        for ask_price in sorted(depth.sell_orders.keys()):
            if ask_price >= fair - min_take_edge:
                break   # asks only get more expensive from here
            if buy_capacity <= 0:
                break
            available_volume = -depth.sell_orders[ask_price]   # sells are stored as negative
            quantity = min(available_volume, buy_capacity)
            if quantity > 0:
                orders.append(Order(product, ask_price, quantity))
                buy_capacity -= quantity

        # ---- Sell into bids that are too expensive ----
        for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_price <= fair + min_take_edge:
                break   # bids only get cheaper from here
            if sell_capacity <= 0:
                break
            available_volume = depth.buy_orders[bid_price]
            quantity = min(available_volume, sell_capacity)
            if quantity > 0:
                orders.append(Order(product, bid_price, -quantity))
                sell_capacity -= quantity

        return orders, buy_capacity, sell_capacity

    @staticmethod
    def _phase_flatten(product, depth, fair, position, buy_capacity, sell_capacity):
        """
        Phase B: 0-EV flatten.

        If we're long (position > 0), sell into any bid AT OR ABOVE
        fair. If we're short (position < 0), buy any ask AT OR BELOW
        fair. We never cross below fair on the sell side or above fair
        on the buy side - the trades are strictly zero-expected-value.

        This is only used when `|position| >= soft_cap` (the caller
        gates it). The goal is to de-risk, not to make money.
        """
        orders = []
        fair_int = int(round(fair))

        if position > 0:
            # We're long -> sell down toward flat
            amount_to_reduce = min(position, sell_capacity)
            for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
                if amount_to_reduce <= 0:
                    break
                if bid_price < fair_int:
                    break   # selling below fair would be -EV
                available_volume = depth.buy_orders[bid_price]
                quantity = min(available_volume, amount_to_reduce)
                if quantity > 0:
                    orders.append(Order(product, bid_price, -quantity))
                    sell_capacity -= quantity
                    amount_to_reduce -= quantity

        elif position < 0:
            # We're short -> buy up toward flat
            amount_to_reduce = min(-position, buy_capacity)
            for ask_price in sorted(depth.sell_orders.keys()):
                if amount_to_reduce <= 0:
                    break
                if ask_price > fair_int:
                    break   # buying above fair would be -EV
                available_volume = -depth.sell_orders[ask_price]
                quantity = min(available_volume, amount_to_reduce)
                if quantity > 0:
                    orders.append(Order(product, ask_price, quantity))
                    buy_capacity -= quantity
                    amount_to_reduce -= quantity

        return orders, buy_capacity, sell_capacity

    @staticmethod
    def _phase_make(product, depth, fair, position, buy_capacity, sell_capacity, cfg):
        """
        Phase C: post maker bid and ask.

        Pricing:
            1. Start with "beat by N": bid = best_bid + N, ask = best_ask - N.
               If the book is empty on a side, fall back to fair +/- 1.
            2. Clamp strictly inside fair: bid < fair, ask > fair. (This
               is the safe starting point before any bias is applied.)
            3. Add `quote_bias_ticks` to BOTH bid and ask. Positive bias
               = lean long: bid becomes more aggressive (fills more
               often, including inside the spread or above fair), ask
               becomes less aggressive (fewer sells).
            4. If `bias_clamp_to_fair` is True, re-clamp the biased bid
               strictly below fair (and ask strictly above). This makes
               bias "safe" but reduces its impact on tight books.

        Inventory pressure:
            If |position - 0| exceeds `soft_cap`, we scale pressure:
                scaling = (|pos| - soft_cap) / (pos_limit - soft_cap)
            With scaling in [0, 1], we pull the quote on the "wrong"
            side toward fair by `scaling * distance_to_fair` ticks, and
            haircut the volume on that side by `(1 - scaling)`.

            pressure_mode controls WHICH side gets pressured:
                "symmetric" : both long-excess and short-excess
                "long_bias" : only short-excess (we want to stay long)
                "off"       : no pressure (use make_portion only)
        """
        orders = []
        position_limit = cfg["position_limit"]

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        # ---- Step 1+2: starting bid and ask, clamped strictly inside fair ----
        bid_price, ask_price = Trader._maker_start_prices(
            best_bid, best_ask, fair, cfg["make_beat_ticks"],
        )

        # ---- Step 3+4: apply quote bias (and optional safety clamp) ----
        bias = cfg["quote_bias_ticks"]
        bid_price = bid_price + bias
        ask_price = ask_price + bias

        if cfg["bias_clamp_to_fair"]:
            max_safe_bid = int(math.floor(fair)) - 1
            min_safe_ask = int(math.ceil(fair)) + 1
            if bid_price > max_safe_bid:
                bid_price = max_safe_bid
            if ask_price < min_safe_ask:
                ask_price = min_safe_ask

        # ---- Maker volumes (before pressure haircut) ----
        # Each side gets: remaining_capacity * make_portion * side_frac / 0.5
        # The / 0.5 normalizer means bid_frac=0.5 gives 100% of the portion
        # (symmetric default), while bid_frac=0.7 gives 140% bid / 60% ask.
        make_portion = cfg["make_portion"]
        bid_frac = cfg["bid_frac"]
        ask_frac = cfg["ask_frac"]

        bid_volume = int(buy_capacity * make_portion * bid_frac / 0.5)
        ask_volume = int(sell_capacity * make_portion * ask_frac / 0.5)
        bid_volume = min(bid_volume, buy_capacity)
        ask_volume = min(ask_volume, sell_capacity)

        # ---- Inventory pressure ----
        bid_price, ask_price, bid_volume, ask_volume = Trader._apply_pressure(
            bid_price, ask_price, bid_volume, ask_volume,
            position, fair, position_limit, cfg,
        )

        # ---- Emit ----
        if bid_volume > 0:
            orders.append(Order(product, bid_price, bid_volume))
        if ask_volume > 0:
            orders.append(Order(product, ask_price, -ask_volume))
        return orders

    @staticmethod
    def _maker_start_prices(best_bid, best_ask, fair, beat_ticks):
        """
        Compute the starting bid and ask prices (before bias), clamped
        to stay strictly inside fair.

        Starting logic:
            bid = best_bid + beat_ticks   (or fair-1 if book is empty)
            ask = best_ask - beat_ticks   (or fair+1 if book is empty)
        Then clamp bid to be at most floor(fair)-1 and ask to be at
        least ceil(fair)+1.
        """
        max_safe_bid = int(math.floor(fair)) - 1
        min_safe_ask = int(math.ceil(fair)) + 1

        if best_bid is not None:
            bid_price = best_bid + beat_ticks
        else:
            bid_price = max_safe_bid

        if best_ask is not None:
            ask_price = best_ask - beat_ticks
        else:
            ask_price = min_safe_ask

        if bid_price > max_safe_bid:
            bid_price = max_safe_bid
        if ask_price < min_safe_ask:
            ask_price = min_safe_ask

        return bid_price, ask_price

    @staticmethod
    def _apply_pressure(bid_price, ask_price, bid_volume, ask_volume,
                        position, fair, position_limit, cfg):
        """
        Pull quotes toward fair and haircut volume on the side that
        matches an over-softcap position. See _phase_make docstring
        for the full spec.
        """
        soft_cap = cfg["soft_cap"]
        pressure_mode = cfg["pressure_mode"]

        abs_position = abs(position)
        if pressure_mode == "off" or abs_position <= soft_cap:
            return bid_price, ask_price, bid_volume, ask_volume

        # How excessive is the position? 0 at softcap, 1 at max.
        excess = (abs_position - soft_cap) / float(position_limit - soft_cap)
        if excess > 1.0:
            excess = 1.0

        is_long = position > 0
        is_short = position < 0

        # Decide whether to pressure this side based on mode
        if pressure_mode == "long_bias":
            pressure_long = False              # let drift run when long
            pressure_short = is_short          # fight shorts only
        else:   # "symmetric"
            pressure_long = is_long
            pressure_short = is_short

        if pressure_long:
            # Pull the ask toward fair, haircut bid size so we stop
            # buying more.
            ask_distance = ask_price - fair
            shift = int(round(excess * ask_distance))
            new_ask = ask_price - shift
            min_safe_ask = int(math.ceil(fair)) + 1
            if new_ask < min_safe_ask:
                new_ask = min_safe_ask
            ask_price = new_ask
            bid_volume = int(bid_volume * (1.0 - excess))

        elif pressure_short:
            # Pull the bid toward fair, haircut ask size.
            bid_distance = fair - bid_price
            shift = int(round(excess * bid_distance))
            new_bid = bid_price + shift
            max_safe_bid = int(math.floor(fair)) - 1
            if new_bid > max_safe_bid:
                new_bid = max_safe_bid
            bid_price = new_bid
            ask_volume = int(ask_volume * (1.0 - excess))

        return bid_price, ask_price, bid_volume, ask_volume

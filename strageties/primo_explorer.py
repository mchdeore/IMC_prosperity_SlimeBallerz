"""
Primo Explorer - research/sweep trader
=======================================

A direct clone of `primo_v3.py` with four additions, all opt-in so that
with no env-var overrides and no new knob values, it produces the EXACT
same orders as `primo_v3.py`:

  1. Env-var JSON config overrides. Set one of these env vars to a JSON
     object before launching the backtester, and the keys inside are
     merged on top of the baseline config dicts defined here.

         EXPL_ACO_CFG     -> merged onto ACO_CFG
         EXPL_IPR_A_CFG   -> merged onto IPR_A_CFG
         EXPL_IPR_B_CFG   -> merged onto IPR_B_CFG
         EXPL_GLOBAL      -> merged onto GLOBAL

  2. New config keys (all default to no-op):

         long_take_edge  (int|None, IPR): asymmetric take edge on the
             ASK side only (aggressive buying when price is drifting up).
             None -> use `min_take_edge` symmetrically.

         multi_level  (list|None): if set, overrides single-level maker.
             Each entry is [offset_ticks, volume_fraction], e.g.
             [[1, 0.6], [3, 0.4]] posts 60% of maker vol at best+1 and
             40% at best+3. Volume fractions should sum to ~1.0.

         time_edge_ramp  (dict|None): ramps `min_take_edge` linearly
             from `start_edge` at t=0 to `end_edge` at `end_ts`:
             {"start_edge": 1, "end_edge": -2, "end_ts": 10000}

         force_mode  (str|None, IPR only): if set to "A" or "B",
             forces the IPR dispatcher into that mode regardless of
             bail state. Used for IPR-B-solo tests.

  3. Order-phase tagging (stdout). When `GLOBAL["order_log"] = True`,
     every emitted order prints a line like:

         [ORDER] t=100 p=IPR phase=take_pos side=B price=7499 qty=5 fair=7500.0 pos=0

     Used by test_06_hold_time and test_07_pnl_attribution. Off by
     default to keep normal runs clean.

  4. Bail-threshold override for slope=0.003 cheats. When an env var
     overrides `slope`, the baseline `bail_dev_threshold=13` is a bad
     signal (fair always diverges far from quotes). Pass your own
     threshold in `EXPL_IPR_A_CFG`.

The strategy logic itself is unchanged from primo_v3 where the new
knobs are not activated.
"""

from datamodel import OrderDepth, TradingState, Order
import json
import math
import os


# -----------------------------------------------------------------------
# Product name constants
# -----------------------------------------------------------------------
ACO = "ASH_COATED_OSMIUM"
IPR = "INTARIAN_PEPPER_ROOT"


# =======================================================================
# CONFIG BLOCKS (same baseline as primo_v3)
# =======================================================================

ACO_CFG = {
    "position_limit":     80,
    "soft_cap":           75,
    "make_portion":       0.80,
    "bid_frac":           0.50,
    "ask_frac":           0.50,
    "make_beat_ticks":    1,
    "quote_bias_ticks":   0,
    "bias_clamp_to_fair": True,
    "min_take_edge":      1,
    "fair_levels":        [1, 2, 3],
    "ema_alpha_new":      0.10,
    "anchor":             10_000,
    "flatten_at_fair":    True,
    "pressure_mode":      "symmetric",
    # --- new knobs (defaults match primo_v3 behavior: no-op) ---
    "long_take_edge":     None,
    "multi_level":        None,
    "time_edge_ramp":     None,
}


IPR_A_CFG = {
    "position_limit":     80,
    "soft_cap":           75,
    "make_portion":       0.90,
    "bid_frac":           0.70,
    "ask_frac":           0.30,
    "make_beat_ticks":    1,
    "quote_bias_ticks":   3,     # matches primo_v3 current baseline
    "bias_clamp_to_fair": True,
    "min_take_edge":      1,
    "slope":              0.001,
    "flatten_at_fair":    True,
    "pressure_mode":      "long_bias",
    "bail_dev_threshold": 13,
    "bail_consecutive":   5,
    "bail_latch":         True,
    # --- new knobs ---
    "long_take_edge":     None,
    "multi_level":        None,
    "time_edge_ramp":     None,
}


IPR_B_CFG = {
    "position_limit":     80,
    "soft_cap":           75,
    "make_portion":       0.90,
    "bid_frac":           0.50,
    "ask_frac":           0.50,
    "make_beat_ticks":    1,
    "quote_bias_ticks":   0,
    "bias_clamp_to_fair": False,
    "min_take_edge":      1,
    "fair_levels":        [2, 3],
    "roc_window":         20,
    "flatten_at_fair":    True,
    "pressure_mode":      "symmetric",
    "max_skew_ticks":     3,
    "skew_per_roc_unit":  1000,
    # --- new knobs ---
    "long_take_edge":     None,
    "multi_level":        None,
    "time_edge_ramp":     None,
}


GLOBAL = {
    "active":     "BOTH",
    "verbose":    False,
    "order_log":  False,     # if True, every emitted order prints a [ORDER] line
    "force_mode": None,      # "A" | "B" | None (overrides IPR dispatcher)
}


# =======================================================================
# ENV-VAR CONFIG OVERRIDES
# =======================================================================

def _merge_env_override(env_var_name, baseline_dict):
    """
    If the named env var is set to a JSON object, merge its keys onto
    baseline_dict (in place). Returns True if anything was merged.
    """
    raw = os.environ.get(env_var_name, "").strip()
    if not raw:
        return False
    try:
        overrides = json.loads(raw)
    except Exception as exc:
        raise ValueError(
            "{} is not valid JSON: {}".format(env_var_name, exc)
        ) from exc
    if not isinstance(overrides, dict):
        raise ValueError("{} must be a JSON object".format(env_var_name))
    baseline_dict.update(overrides)
    return True


_merge_env_override("EXPL_ACO_CFG",   ACO_CFG)
_merge_env_override("EXPL_IPR_A_CFG", IPR_A_CFG)
_merge_env_override("EXPL_IPR_B_CFG", IPR_B_CFG)
_merge_env_override("EXPL_GLOBAL",    GLOBAL)


# =======================================================================
# TRADER CLASS
# =======================================================================

class Trader:

    # -------------------------------------------------------------------
    # State serialization helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _load_state(trader_data_str):
        if not trader_data_str:
            return {}
        try:
            return json.loads(trader_data_str)
        except Exception:
            return {}

    @staticmethod
    def _save_state(state_dict):
        return json.dumps(state_dict)

    def _log(self, message):
        if GLOBAL.get("verbose"):
            print(message)

    # -------------------------------------------------------------------
    # Order-phase logging (for tests 06 and 07)
    # -------------------------------------------------------------------

    @staticmethod
    def _log_orders(product, phase, orders, fair, position, timestamp):
        """If GLOBAL['order_log'] is True, print one line per submitted order."""
        if not GLOBAL.get("order_log"):
            return
        short_p = "ACO" if product == ACO else ("IPR" if product == IPR else product)
        for order in orders:
            side = "B" if order.quantity > 0 else "S"
            qty = abs(order.quantity)
            print(
                "[ORDER] t={} p={} phase={} side={} price={} qty={} fair={:.1f} pos={}".format(
                    timestamp, short_p, phase, side, order.price, qty, fair, position
                )
            )

    # -------------------------------------------------------------------
    # MAIN ENTRY POINT
    # -------------------------------------------------------------------

    def run(self, state: TradingState):
        saved_state = self._load_state(state.traderData)
        result = {}

        active = GLOBAL.get("active", "BOTH")
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
    # STRATEGY 1: ACO
    # ===================================================================

    def _run_aco(self, depth, position, timestamp, saved_state):
        cfg = ACO_CFG
        aco_state = saved_state.setdefault("aco", {})

        previous_fair = aco_state.get("fair")
        book_fair = self._aco_book_fair(depth, cfg["fair_levels"])
        fair = self._aco_update_fair(book_fair, previous_fair, cfg)
        aco_state["fair"] = fair

        buy_capacity = cfg["position_limit"] - position
        sell_capacity = cfg["position_limit"] + position

        all_orders = []

        # Effective take edge (with optional time ramp).
        effective_take_edge = self._effective_take_edge(cfg, timestamp)

        take_orders, buy_capacity, sell_capacity = self._phase_take(
            ACO, depth, fair, buy_capacity, sell_capacity,
            min_take_edge=effective_take_edge,
            long_take_edge=cfg.get("long_take_edge"),
        )
        all_orders.extend(take_orders)
        self._log_orders(ACO, "take_pos", take_orders, fair, position, timestamp)

        if cfg["flatten_at_fair"] and abs(position) >= cfg["soft_cap"]:
            flatten_orders, buy_capacity, sell_capacity = self._phase_flatten(
                ACO, depth, fair, position, buy_capacity, sell_capacity,
            )
            all_orders.extend(flatten_orders)
            self._log_orders(ACO, "flatten", flatten_orders, fair, position, timestamp)

        make_orders = self._phase_make(
            ACO, depth, fair, position, buy_capacity, sell_capacity, cfg,
        )
        all_orders.extend(make_orders)
        self._log_orders(ACO, "make", make_orders, fair, position, timestamp)

        self._log("[ACO] t={} pos={:+d} fair={:.1f} orders={}".format(
            timestamp, position, fair, len(all_orders),
        ))
        return all_orders

    @staticmethod
    def _aco_book_fair(depth, levels):
        sorted_bids = sorted(depth.buy_orders.keys(), reverse=True)
        sorted_asks = sorted(depth.sell_orders.keys())

        if not sorted_bids or not sorted_asks:
            return None

        bid_prices = []
        for level in levels:
            index = level - 1
            if index < len(sorted_bids):
                bid_prices.append(sorted_bids[index])

        ask_prices = []
        for level in levels:
            index = level - 1
            if index < len(sorted_asks):
                ask_prices.append(sorted_asks[index])

        if not bid_prices or not ask_prices:
            return None

        bid_mid = sum(bid_prices) / len(bid_prices)
        ask_mid = sum(ask_prices) / len(ask_prices)
        return (bid_mid + ask_mid) / 2.0

    @staticmethod
    def _aco_update_fair(book_fair, previous_fair, cfg):
        alpha = cfg["ema_alpha_new"]

        if book_fair is not None:
            if previous_fair is None:
                return book_fair
            return alpha * book_fair + (1.0 - alpha) * previous_fair

        if previous_fair is not None:
            return previous_fair

        return float(cfg["anchor"])

    # ===================================================================
    # IPR DISPATCH
    # ===================================================================

    def _run_ipr(self, depth, position, timestamp, saved_state):
        ipr_state = saved_state.setdefault("ipr", {"mode": "A"})

        # Optional force-mode override (for IPR-B solo tests).
        force_mode = GLOBAL.get("force_mode")
        if force_mode in ("A", "B"):
            ipr_state["mode"] = force_mode

        mode = ipr_state.get("mode", "A")
        if mode == "A":
            return self._run_ipr_linear(depth, position, timestamp, ipr_state)
        return self._run_ipr_momentum(depth, position, timestamp, ipr_state)

    # ===================================================================
    # STRATEGY 2: IPR-A linear drift
    # ===================================================================

    def _run_ipr_linear(self, depth, position, timestamp, ipr_state):
        cfg = IPR_A_CFG

        if "initial_fair" not in ipr_state:
            mid = self._simple_mid(depth)
            if mid is None:
                return []
            ipr_state["initial_fair"] = mid
            ipr_state["initial_ts"] = timestamp

        initial_fair = ipr_state["initial_fair"]
        initial_ts = ipr_state["initial_ts"]
        fair = initial_fair + cfg["slope"] * (timestamp - initial_ts)

        # Only auto-bail when not in force_mode.
        if GLOBAL.get("force_mode") is None:
            self._ipr_a_check_bail(depth, fair, ipr_state, cfg)
            if ipr_state.get("mode") == "B":
                return self._run_ipr_momentum(depth, position, timestamp, ipr_state)

        buy_capacity = cfg["position_limit"] - position
        sell_capacity = cfg["position_limit"] + position
        all_orders = []

        effective_take_edge = self._effective_take_edge(cfg, timestamp)

        take_orders, buy_capacity, sell_capacity = self._phase_take(
            IPR, depth, fair, buy_capacity, sell_capacity,
            min_take_edge=effective_take_edge,
            long_take_edge=cfg.get("long_take_edge"),
        )
        all_orders.extend(take_orders)
        self._log_orders(IPR, "take_pos", take_orders, fair, position, timestamp)

        if cfg["flatten_at_fair"] and abs(position) >= cfg["soft_cap"]:
            flatten_orders, buy_capacity, sell_capacity = self._phase_flatten(
                IPR, depth, fair, position, buy_capacity, sell_capacity,
            )
            all_orders.extend(flatten_orders)
            self._log_orders(IPR, "flatten", flatten_orders, fair, position, timestamp)

        make_orders = self._phase_make(
            IPR, depth, fair, position, buy_capacity, sell_capacity, cfg,
        )
        all_orders.extend(make_orders)
        self._log_orders(IPR, "make", make_orders, fair, position, timestamp)

        self._log("[IPR-A] t={} pos={:+d} fair={:.1f} orders={}".format(
            timestamp, position, fair, len(all_orders),
        ))
        return all_orders

    def _ipr_a_check_bail(self, depth, fair, ipr_state, cfg):
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        if best_bid is None or best_ask is None:
            return

        bid_distance = abs(best_bid - fair)
        ask_distance = abs(best_ask - fair)
        closest_distance = min(bid_distance, ask_distance)

        history = ipr_state.setdefault("dev_history", [])
        history.append(closest_distance)

        keep_n = cfg["bail_consecutive"]
        if len(history) > keep_n:
            del history[:-keep_n]

        if len(history) >= keep_n:
            all_exceed = all(d > cfg["bail_dev_threshold"] for d in history)
            if all_exceed:
                ipr_state["mode"] = "B"
                self._log(
                    "[IPR-A] BAIL -> switching to Strategy B "
                    "(devs={}, threshold={})".format(
                        history, cfg["bail_dev_threshold"]
                    )
                )

    # ===================================================================
    # STRATEGY 3: IPR-B momentum fallback
    # ===================================================================

    def _run_ipr_momentum(self, depth, position, timestamp, ipr_state):
        cfg = IPR_B_CFG

        fair_history = ipr_state.setdefault("fair_history", [])
        ts_history = ipr_state.setdefault("ts_history", [])
        previous_fair = fair_history[-1] if fair_history else None
        previous_ts = ts_history[-1] if ts_history else None

        roc = self._ipr_b_compute_roc(fair_history, ts_history, cfg["roc_window"])

        book_fair = self._aco_book_fair(depth, cfg["fair_levels"])
        if book_fair is not None:
            fair = book_fair
        elif previous_fair is not None and previous_ts is not None:
            fair = previous_fair + roc * (timestamp - previous_ts)
        else:
            simple = self._simple_mid(depth)
            fair = simple if simple is not None else 10_000.0

        fair_history.append(fair)
        ts_history.append(timestamp)
        if len(fair_history) > cfg["roc_window"] + 1:
            del fair_history[:1]
            del ts_history[:1]

        raw_skew = int(round(roc * cfg["skew_per_roc_unit"]))
        if raw_skew > cfg["max_skew_ticks"]:
            momentum_skew = cfg["max_skew_ticks"]
        elif raw_skew < -cfg["max_skew_ticks"]:
            momentum_skew = -cfg["max_skew_ticks"]
        else:
            momentum_skew = raw_skew

        effective_cfg = dict(cfg)
        effective_cfg["quote_bias_ticks"] = cfg["quote_bias_ticks"] + momentum_skew

        buy_capacity = cfg["position_limit"] - position
        sell_capacity = cfg["position_limit"] + position
        all_orders = []

        effective_take_edge = self._effective_take_edge(cfg, timestamp)

        take_orders, buy_capacity, sell_capacity = self._phase_take(
            IPR, depth, fair, buy_capacity, sell_capacity,
            min_take_edge=effective_take_edge,
            long_take_edge=cfg.get("long_take_edge"),
        )
        all_orders.extend(take_orders)
        self._log_orders(IPR, "take_pos", take_orders, fair, position, timestamp)

        if cfg["flatten_at_fair"] and abs(position) >= cfg["soft_cap"]:
            flatten_orders, buy_capacity, sell_capacity = self._phase_flatten(
                IPR, depth, fair, position, buy_capacity, sell_capacity,
            )
            all_orders.extend(flatten_orders)
            self._log_orders(IPR, "flatten", flatten_orders, fair, position, timestamp)

        make_orders = self._phase_make(
            IPR, depth, fair, position, buy_capacity, sell_capacity, effective_cfg,
        )
        all_orders.extend(make_orders)
        self._log_orders(IPR, "make", make_orders, fair, position, timestamp)

        self._log("[IPR-B] t={} pos={:+d} fair={:.1f} roc={:+.4f} skew={:+d} orders={}".format(
            timestamp, position, fair, roc, momentum_skew, len(all_orders),
        ))
        return all_orders

    @staticmethod
    def _ipr_b_compute_roc(fair_history, ts_history, window):
        if len(fair_history) < 2 or len(ts_history) < 2:
            return 0.0
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
        if not depth.buy_orders or not depth.sell_orders:
            return None
        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        return (best_bid + best_ask) / 2.0

    # ===================================================================
    # SHARED PHASES
    # ===================================================================

    @staticmethod
    def _effective_take_edge(cfg, timestamp):
        """Returns the take edge for this tick, applying time_edge_ramp if set."""
        ramp = cfg.get("time_edge_ramp")
        base = cfg["min_take_edge"]
        if ramp is None:
            return base
        start_edge = ramp.get("start_edge", base)
        end_edge = ramp.get("end_edge", base)
        end_ts = ramp.get("end_ts", 1)
        if end_ts <= 0:
            return end_edge
        fraction = max(0.0, min(1.0, float(timestamp) / float(end_ts)))
        # linear interpolation between start_edge and end_edge
        return start_edge + (end_edge - start_edge) * fraction

    @staticmethod
    def _phase_take(product, depth, fair, buy_capacity, sell_capacity,
                    min_take_edge, long_take_edge=None):
        """
        Phase A: aggressive taker.

        When `long_take_edge` is provided (int), the ASK side of the
        book (which we buy from) uses that edge instead of
        `min_take_edge`. Set `long_take_edge` lower (or negative) to be
        more aggressive buying into drift. `min_take_edge` still
        governs the bid side (how aggressively we sell).
        """
        orders = []
        ask_edge = long_take_edge if long_take_edge is not None else min_take_edge

        for ask_price in sorted(depth.sell_orders.keys()):
            if ask_price >= fair - ask_edge:
                break
            if buy_capacity <= 0:
                break
            available_volume = -depth.sell_orders[ask_price]
            quantity = min(available_volume, buy_capacity)
            if quantity > 0:
                orders.append(Order(product, ask_price, quantity))
                buy_capacity -= quantity

        for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_price <= fair + min_take_edge:
                break
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
        orders = []
        fair_int = int(round(fair))

        if position > 0:
            amount_to_reduce = min(position, sell_capacity)
            for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
                if amount_to_reduce <= 0:
                    break
                if bid_price < fair_int:
                    break
                available_volume = depth.buy_orders[bid_price]
                quantity = min(available_volume, amount_to_reduce)
                if quantity > 0:
                    orders.append(Order(product, bid_price, -quantity))
                    sell_capacity -= quantity
                    amount_to_reduce -= quantity

        elif position < 0:
            amount_to_reduce = min(-position, buy_capacity)
            for ask_price in sorted(depth.sell_orders.keys()):
                if amount_to_reduce <= 0:
                    break
                if ask_price > fair_int:
                    break
                available_volume = -depth.sell_orders[ask_price]
                quantity = min(available_volume, amount_to_reduce)
                if quantity > 0:
                    orders.append(Order(product, ask_price, quantity))
                    buy_capacity -= quantity
                    amount_to_reduce -= quantity

        return orders, buy_capacity, sell_capacity

    @staticmethod
    def _phase_make(product, depth, fair, position, buy_capacity, sell_capacity, cfg):
        """Phase C: post maker quotes. See primo_v3 for full spec.

        If `cfg["multi_level"]` is a non-empty list of
        `[offset_ticks, volume_fraction]` entries, we emit one quote
        per entry on each side.
        """
        orders = []
        position_limit = cfg["position_limit"]

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        # Starting prices for the DEFAULT (L1) quote - used as the
        # anchor for multi-level offsets too.
        base_bid_price, base_ask_price = Trader._maker_start_prices(
            best_bid, best_ask, fair, cfg["make_beat_ticks"],
        )

        bias = cfg.get("quote_bias_ticks", 0)
        base_bid_price = base_bid_price + bias
        base_ask_price = base_ask_price + bias

        if cfg.get("bias_clamp_to_fair", True):
            max_safe_bid = int(math.floor(fair)) - 1
            min_safe_ask = int(math.ceil(fair)) + 1
            if base_bid_price > max_safe_bid:
                base_bid_price = max_safe_bid
            if base_ask_price < min_safe_ask:
                base_ask_price = min_safe_ask

        make_portion = cfg["make_portion"]
        bid_frac = cfg["bid_frac"]
        ask_frac = cfg["ask_frac"]

        total_bid_volume = int(buy_capacity * make_portion * bid_frac / 0.5)
        total_ask_volume = int(sell_capacity * make_portion * ask_frac / 0.5)
        total_bid_volume = min(total_bid_volume, buy_capacity)
        total_ask_volume = min(total_ask_volume, sell_capacity)

        # Apply pressure to the base prices and total volumes.
        (base_bid_price, base_ask_price,
         total_bid_volume, total_ask_volume) = Trader._apply_pressure(
            base_bid_price, base_ask_price, total_bid_volume, total_ask_volume,
            position, fair, position_limit, cfg,
        )

        multi = cfg.get("multi_level")
        if multi and isinstance(multi, list) and len(multi) > 0:
            # Split volume across multiple levels. Layer i has:
            #   bid_price = base_bid_price - (offset_i - make_beat_ticks)   (deeper)
            #   ask_price = base_ask_price + (offset_i - make_beat_ticks)   (deeper)
            # offset_i = make_beat_ticks is the "base" level (no deeper).
            beat = cfg["make_beat_ticks"]
            total_frac = sum(entry[1] for entry in multi)
            if total_frac <= 0:
                total_frac = 1.0
            for entry in multi:
                offset_i, frac_i = entry[0], entry[1]
                depth_ticks = offset_i - beat   # 0 = base level
                bid_i = base_bid_price - depth_ticks
                ask_i = base_ask_price + depth_ticks
                bv = int(total_bid_volume * (frac_i / total_frac))
                av = int(total_ask_volume * (frac_i / total_frac))
                if bv > 0:
                    orders.append(Order(product, bid_i, bv))
                if av > 0:
                    orders.append(Order(product, ask_i, -av))
            return orders

        # Single-level (default, identical to primo_v3).
        if total_bid_volume > 0:
            orders.append(Order(product, base_bid_price, total_bid_volume))
        if total_ask_volume > 0:
            orders.append(Order(product, base_ask_price, -total_ask_volume))
        return orders

    @staticmethod
    def _maker_start_prices(best_bid, best_ask, fair, beat_ticks):
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
        soft_cap = cfg["soft_cap"]
        pressure_mode = cfg["pressure_mode"]

        abs_position = abs(position)
        if pressure_mode == "off" or abs_position <= soft_cap:
            return bid_price, ask_price, bid_volume, ask_volume

        excess = (abs_position - soft_cap) / float(position_limit - soft_cap)
        if excess > 1.0:
            excess = 1.0

        is_long = position > 0
        is_short = position < 0

        if pressure_mode == "long_bias":
            pressure_long = False
            pressure_short = is_short
        else:
            pressure_long = is_long
            pressure_short = is_short

        if pressure_long:
            ask_distance = ask_price - fair
            shift = int(round(excess * ask_distance))
            new_ask = ask_price - shift
            min_safe_ask = int(math.ceil(fair)) + 1
            if new_ask < min_safe_ask:
                new_ask = min_safe_ask
            ask_price = new_ask
            bid_volume = int(bid_volume * (1.0 - excess))

        elif pressure_short:
            bid_distance = fair - bid_price
            shift = int(round(excess * bid_distance))
            new_bid = bid_price + shift
            max_safe_bid = int(math.floor(fair)) - 1
            if new_bid > max_safe_bid:
                new_bid = max_safe_bid
            bid_price = new_bid
            ask_volume = int(ask_volume * (1.0 - excess))

        return bid_price, ask_price, bid_volume, ask_volume

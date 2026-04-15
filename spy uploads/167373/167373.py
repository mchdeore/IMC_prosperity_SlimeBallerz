"""
Spy: INTARIAN_PEPPER_ROOT Experiment Runner
============================================
Systematically probes bot behavior for IPR by cycling through 4 experiment
types on a 30-tick rotation:

  1. Edge sweep   (12 ticks) — quote offsets 1-15 from wall_mid
  2. Volume sweep  (7 ticks) — vary lot size at fixed edge=3
  3. Take strategy  (4 ticks) — passive / aggressive / overbid / wallmatch
  4. Skew test      (7 ticks) — vary bid/ask volume ratio

IPR-specific: no anchor (price drifts ~1000/day), tracks return since last
tick to correlate fills with momentum, uses tighter baseline_edge=3.

Submit to the Prosperity platform. Parse logs with log_parser.py.
Only trades INTARIAN_PEPPER_ROOT; ignores other products.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional
import json
import math

PRODUCT = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80
FLATTEN_THRESHOLD = 10
BASELINE_EDGE = 3
TRADER_DATA_LIMIT = 45_000

EDGE_OFFSETS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15]
VOLUME_SIZES = [1, 2, 5, 10, 15, 20, 30]
TAKE_MODES = ["passive", "aggressive", "overbid", "wallmatch"]
SKEW_RATIOS = [(100, 0), (80, 20), (60, 40), (50, 50), (40, 60), (20, 80), (0, 100)]

SCHEDULE = (
    [("edge", i) for i in range(len(EDGE_OFFSETS))]
    + [("volume", i) for i in range(len(VOLUME_SIZES))]
    + [("take", i) for i in range(len(TAKE_MODES))]
    + [("skew", i) for i in range(len(SKEW_RATIOS))]
)
CYCLE_LEN = len(SCHEDULE)


def safe_order(product, price, qty, position):
    if qty > 0:
        qty = min(qty, POSITION_LIMIT - position)
    elif qty < 0:
        qty = max(qty, -(POSITION_LIMIT + position))
    if qty == 0:
        return None
    return Order(product, int(price), int(qty))


def wall_mid(depth):
    if not depth.buy_orders or not depth.sell_orders:
        return None, None, None
    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    bid_wall = max(depth.buy_orders.items(), key=lambda x: abs(x[1]))[0]
    ask_wall = max(depth.sell_orders.items(), key=lambda x: abs(x[1]))[0]
    wm = (bid_wall + ask_wall) / 2.0
    wm = max(best_bid, min(best_ask, wm))
    return wm, bid_wall, ask_wall


class Trader:

    def bid(self) -> int:
        return 0

    @staticmethod
    def _load(td):
        if not td:
            return {}
        try:
            return json.loads(td)
        except Exception:
            return {}

    @staticmethod
    def _save(state):
        s = json.dumps(state)
        if len(s) > TRADER_DATA_LIMIT:
            state.pop("cum", None)
            s = json.dumps(state)
        return s

    def run(self, state: TradingState):
        saved = self._load(state.traderData)
        tick = saved.get("tick", 0) + 1
        saved["tick"] = tick
        sched_idx = saved.get("si", 0)
        cum = saved.get("cum", {})

        result: Dict[str, List[Order]] = {}
        for p in state.order_depths:
            result[p] = []

        if PRODUCT not in state.order_depths:
            return result, 0, self._save(saved)

        depth = state.order_depths[PRODUCT]
        position = state.position.get(PRODUCT, 0)
        orders: List[Order] = []

        wm, bid_wall_px, ask_wall_px = wall_mid(depth)
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        # Track return since last tick for momentum correlation
        prev_wm = saved.get("prev_wm")
        tick_return = None
        if prev_wm is not None and wm is not None:
            tick_return = round(wm - prev_wm, 2)
        if wm is not None:
            saved["prev_wm"] = round(wm, 2)

        # ── Check previous probe fill ─────────────────────────────────
        prev = saved.get("prev")
        prev_result = None
        if prev and PRODUCT in state.own_trades:
            fills = state.own_trades[PRODUCT]
            matched = [f for f in fills if f.price == prev["px"]]
            filled = len(matched) > 0
            fill_qty = sum(f.quantity for f in matched) if filled else 0

            key = f"{prev['exp']}_{prev['param']}_{prev['side']}"
            entry = cum.setdefault(key, {"n": 0, "fills": 0, "qty_filled": 0})
            entry["n"] += 1
            if filled:
                entry["fills"] += 1
                entry["qty_filled"] += abs(fill_qty)

            prev_result = {"key": key, "filled": filled, "fill_qty": fill_qty, "momentum": tick_return}
        saved["cum"] = cum

        # ── Flatten if position drifted ───────────────────────────────
        if abs(position) > FLATTEN_THRESHOLD and wm is not None:
            if position > 0 and best_bid is not None:
                o = safe_order(PRODUCT, best_bid, -min(position, 5), position)
                if o:
                    orders.append(o)
            elif position < 0 and best_ask is not None:
                o = safe_order(PRODUCT, best_ask, min(abs(position), 5), position)
                if o:
                    orders.append(o)
            saved["prev"] = None

        # ── Run experiment ────────────────────────────────────────────
        elif wm is not None:
            exp_type, exp_idx = SCHEDULE[sched_idx % CYCLE_LEN]
            probe = None

            if exp_type == "edge":
                offset = EDGE_OFFSETS[exp_idx]
                bid_px = math.floor(wm - offset)
                ask_px = math.ceil(wm + offset)
                side = "bid" if tick % 2 == 0 else "ask"
                if side == "bid":
                    o = safe_order(PRODUCT, bid_px, 1, position)
                    if o:
                        orders.append(o)
                        probe = {"exp": "edge", "param": offset, "side": "bid", "px": bid_px, "qty": 1}
                else:
                    o = safe_order(PRODUCT, ask_px, -1, position)
                    if o:
                        orders.append(o)
                        probe = {"exp": "edge", "param": offset, "side": "ask", "px": ask_px, "qty": 1}

            elif exp_type == "volume":
                vol = VOLUME_SIZES[exp_idx]
                bid_px = math.floor(wm - BASELINE_EDGE)
                ask_px = math.ceil(wm + BASELINE_EDGE)
                side = "bid" if tick % 2 == 0 else "ask"
                if side == "bid":
                    o = safe_order(PRODUCT, bid_px, vol, position)
                    if o:
                        orders.append(o)
                        probe = {"exp": "vol", "param": vol, "side": "bid", "px": bid_px, "qty": vol}
                else:
                    o = safe_order(PRODUCT, ask_px, -vol, position)
                    if o:
                        orders.append(o)
                        probe = {"exp": "vol", "param": vol, "side": "ask", "px": ask_px, "qty": vol}

            elif exp_type == "take":
                mode = TAKE_MODES[exp_idx]
                if mode == "passive":
                    px = math.floor(wm - BASELINE_EDGE) if tick % 2 == 0 else math.ceil(wm + BASELINE_EDGE)
                    qty = 1 if tick % 2 == 0 else -1
                    side = "bid" if qty > 0 else "ask"
                    o = safe_order(PRODUCT, px, qty, position)
                    if o:
                        orders.append(o)
                        probe = {"exp": "take", "param": mode, "side": side, "px": px, "qty": 1}
                elif mode == "aggressive":
                    if best_ask is not None and best_ask < wm - 1:
                        o = safe_order(PRODUCT, best_ask, 1, position)
                        if o:
                            orders.append(o)
                            probe = {"exp": "take", "param": mode, "side": "bid", "px": best_ask, "qty": 1}
                    elif best_bid is not None and best_bid > wm + 1:
                        o = safe_order(PRODUCT, best_bid, -1, position)
                        if o:
                            orders.append(o)
                            probe = {"exp": "take", "param": mode, "side": "ask", "px": best_bid, "qty": 1}
                elif mode == "overbid":
                    if best_bid is not None and best_ask is not None:
                        if tick % 2 == 0:
                            px = best_bid + 1
                            if px < wm:
                                o = safe_order(PRODUCT, px, 1, position)
                                if o:
                                    orders.append(o)
                                    probe = {"exp": "take", "param": mode, "side": "bid", "px": px, "qty": 1}
                        else:
                            px = best_ask - 1
                            if px > wm:
                                o = safe_order(PRODUCT, px, -1, position)
                                if o:
                                    orders.append(o)
                                    probe = {"exp": "take", "param": mode, "side": "ask", "px": px, "qty": 1}
                elif mode == "wallmatch":
                    if bid_wall_px is not None and ask_wall_px is not None:
                        if tick % 2 == 0:
                            o = safe_order(PRODUCT, bid_wall_px, 1, position)
                            if o:
                                orders.append(o)
                                probe = {"exp": "take", "param": mode, "side": "bid", "px": bid_wall_px, "qty": 1}
                        else:
                            o = safe_order(PRODUCT, ask_wall_px, -1, position)
                            if o:
                                orders.append(o)
                                probe = {"exp": "take", "param": mode, "side": "ask", "px": ask_wall_px, "qty": 1}

            elif exp_type == "skew":
                bid_pct, ask_pct = SKEW_RATIOS[exp_idx]
                bid_px = math.floor(wm - BASELINE_EDGE)
                ask_px = math.ceil(wm + BASELINE_EDGE)
                base_vol = 5
                bid_vol = max(1, round(base_vol * bid_pct / 100)) if bid_pct > 0 else 0
                ask_vol = max(1, round(base_vol * ask_pct / 100)) if ask_pct > 0 else 0
                if bid_vol > 0:
                    o = safe_order(PRODUCT, bid_px, bid_vol, position)
                    if o:
                        orders.append(o)
                if ask_vol > 0:
                    o = safe_order(PRODUCT, ask_px, -ask_vol, position + bid_vol)
                    if o:
                        orders.append(o)
                probe = {"exp": "skew", "param": f"{bid_pct}_{ask_pct}", "side": "both", "px": bid_px, "qty": bid_vol + ask_vol}

            saved["prev"] = probe
            sched_idx = (sched_idx + 1) % CYCLE_LEN
            saved["si"] = sched_idx

        result[PRODUCT] = orders

        top_cum = {}
        for k, v in sorted(cum.items()):
            rate = round(v["fills"] / v["n"], 3) if v["n"] > 0 else 0
            top_cum[k] = {"n": v["n"], "f": v["fills"], "r": rate}

        print("SPY_IPR|" + json.dumps({
            "t": state.timestamp,
            "tick": tick,
            "pos": position,
            "wm": round(wm, 1) if wm is not None else None,
            "ret": tick_return,
            "bb": best_bid,
            "ba": best_ask,
            "exp": saved.get("prev"),
            "prev": prev_result,
            "cum": top_cum,
        }))

        return result, 0, self._save(saved)


if __name__ == "__main__":
    from datamodel import Listing, Observation, Trade

    def make_state(buys, sells, pos=0, ts=100, td="", own_trades=None):
        od = OrderDepth()
        od.buy_orders = buys
        od.sell_orders = sells
        return TradingState(
            traderData=td, timestamp=ts,
            listings={PRODUCT: Listing(symbol=PRODUCT, product=PRODUCT, denomination="XIRECS")},
            order_depths={PRODUCT: od},
            own_trades=own_trades or {PRODUCT: []},
            market_trades={PRODUCT: []},
            position={PRODUCT: pos},
            observations=Observation({}, {}),
        )

    t = Trader()
    print("=" * 60)
    print("SPY_IPR SMOKE TEST")
    print("=" * 60)

    td = ""
    for i in range(35):
        mid_base = 12000 + i * 10
        s = make_state(
            {mid_base - 3: 17, mid_base - 6: 21, mid_base - 9: 12},
            {mid_base + 3: -11, mid_base + 6: -21, mid_base + 9: -14},
            pos=0, ts=i * 100, td=td,
        )
        r, _, td = t.run(s)
        for o in r.get(PRODUCT, []):
            new_pos = 0 + o.quantity
            assert abs(new_pos) <= POSITION_LIMIT, f"Limit breach: {new_pos}"

    print(f"\n  Completed 35 ticks ({CYCLE_LEN}-tick cycle + extras)")

    saved = json.loads(td)
    cum = saved.get("cum", {})
    print(f"  Accumulated {len(cum)} experiment keys")
    for k, v in sorted(cum.items())[:5]:
        print(f"    {k}: n={v['n']}, fills={v['fills']}")

    prev_wm = saved.get("prev_wm")
    print(f"  Last tracked wall_mid: {prev_wm}")

    print("\n--- Position limit check: pos=75 ---")
    s = make_state(
        {12100: 17, 12094: 21}, {12106: -11, 12112: -21},
        pos=75, ts=5000, td=td,
    )
    r, _, td = t.run(s)
    for o in r.get(PRODUCT, []):
        new_pos = 75 + o.quantity
        assert abs(new_pos) <= POSITION_LIMIT, f"Limit breach: {new_pos}"
    print("  Position limit OK")

    print("\n--- Negative position flatten ---")
    s = make_state(
        {12100: 17, 12094: 21}, {12106: -11, 12112: -21},
        pos=-15, ts=5100, td=td,
    )
    r, _, td = t.run(s)
    has_buy = any(o.quantity > 0 for o in r.get(PRODUCT, []))
    print(f"  Flatten placed buy: {has_buy}")
    assert has_buy, "Should flatten negative position with buy"
    print("  All checks passed")
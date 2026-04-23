"""
Per-tick log of strategy state not present in the backtester Activities log.

The standard backtest ``.log`` activities section already has: day, timestamp,
product, market bid/ask levels (3 deep), mid_price, profit_and_loss.

This recorder adds: round, day (from the backtester env), position, and every order you submit
that tick (quotes) as JSON [[price, quantity], ...] (quantity > 0 buy,
< 0 sell). Join to the activities table on (timestamp, product); include
round and day when merging multiple runs (both come from ``PROSPERITY4BT_*`` env
vars set by the backtester in ``run_backtest``).
"""

from __future__ import annotations

import atexit
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from datamodel import Order, TradingState


def logs_csv_path(name: str = "ticks") -> Path:
    """
    ``LOGS/{name}_{%Y-%m-%d_%H-%M}.csv`` under the repository root (hour:minute only, no seconds).
    Creates ``LOGS`` if needed.
    """
    root = Path(__file__).resolve().parent.parent
    logs = root / "LOGS"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    return logs / f"{name}_{stamp}.csv"


def _env_int(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@dataclass
class TickRecorder:
    """
    Call record_tick(state, orders) once per Trader.run when backtesting.

    Example::

        rec = TickRecorder(auto_save_csv="ticks.csv")
        trader = Trader(tick_recorder=rec)
        # ... run_backtest ...
        df = rec.to_dataframe()
    """

    rows: List[Dict[str, Any]] = field(default_factory=list)
    auto_save_csv: Optional[Union[str, Path]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._manifest_written = False
        if self.auto_save_csv is not None:
            atexit.register(self._atexit_write_csv)

    def clear(self) -> None:
        self.rows.clear()

    def record_tick(
        self,
        state: TradingState,
        orders: Dict[str, List[Order]],
        fair: Optional[Dict[str, float]] = None,
    ) -> None:
        if self.auto_save_csv is not None and not self._manifest_written:
            self._manifest_written = True
            try:
                from .backtest_source_manifest import write_manifest_next_to_tick_csv

                write_manifest_next_to_tick_csv(Path(self.auto_save_csv))
            except Exception:
                pass

        rnd = _env_int("PROSPERITY4BT_ROUND")
        day = _env_int("PROSPERITY4BT_DAY")
        products = set(state.order_depths.keys()) | set(orders.keys())
        fair = fair or {}
        for product in sorted(products):
            od_list = orders.get(product, [])
            fair_val = fair.get(product)
            self.rows.append(
                {
                    "round": rnd,
                    "day": day,
                    "timestamp": state.timestamp,
                    "product": product,
                    "position": state.position.get(product, 0),
                    "quotes_json": json.dumps([[o.price, o.quantity] for o in od_list]),
                    "fair_json": (
                        "" if fair_val is None else json.dumps(float(fair_val))
                    ),
                }
            )

    def record_and_emit(
        self,
        state: TradingState,
        orders: Dict[str, List[Order]],
        fair: Optional[Dict[str, float]] = None,
        *,
        sandbox_stdout: bool = True,
    ) -> None:
        """Record the tick AND print the sandbox ``lambdaLog`` payload.

        Using this single call from a strategy's ``Trader.run`` makes any
        strategy play nicely with the visualizer (fair line) and tick-CSV
        workflows without the strategy hand-rolling the JSON print.

        Pass ``fair={PRODUCT: price, ...}`` with the strategy's own
        computed fair value(s); the visualizer will overlay them on the
        matching product's chart.
        """
        self.record_tick(state, orders, fair=fair)

        if not sandbox_stdout:
            return

        import sys

        payload = {
            "t": state.timestamp,
            "orders": {
                k: [[o.price, o.quantity] for o in v] for k, v in orders.items()
            },
        }
        if fair:
            payload["fair"] = {p: float(v) for p, v in fair.items()}
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def _atexit_write_csv(self) -> None:
        if not self.auto_save_csv or not self.rows:
            return
        path = Path(self.auto_save_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.to_dataframe().to_csv(path, index=False)

    def to_dataframe(self):
        import pandas as pd

        cols = [
            "round", "day", "timestamp", "product",
            "position", "quotes_json", "fair_json",
        ]
        if not self.rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(self.rows)

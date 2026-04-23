# Strategies - authoring guide

This folder holds one `Trader` per file. Every strategy should support
**two modes**:

- **Dev / backtest mode** - instrumented via `MODULES.TickRecorder` so
  the visualizer's fair-line, fills, quotes, and KPI pages all just
  work. Dumps a per-tick CSV into `LOGS/` and prints a single
  `lambdaLog` JSON line per tick so the backtester picks it up.
- **IMC upload mode** - the instrumentation is mechanically stripped
  out so what you paste into the grader is a pure
  `Trader.run(state) -> (orders, conversions, trader_data)` with no
  file I/O, no `MODULES` import, and no `print`.

Follow the two sections below when building a new strategy (or asking
an agent to build one).

---

## 1. Dev / backtest mode - what to include

1. **Path shim** so `MODULES` is importable when the backtester runs
   the strategy with just `STRATEGIES/` on `sys.path`:

   ```python
   import _repo_path  # noqa: F401 - adds repo root for MODULES imports
   ```

2. **Imports**:

   ```python
   from datamodel import OrderDepth, TradingState, Order
   import json
   import math
   from typing import Optional

   from MODULES import TickRecorder, logs_csv_path
   ```

3. **`Trader.__init__`** accepting the three dev-mode flags (copy from
   `ACO_hardcode.py`):

   ```python
   class Trader:
       def __init__(
           self,
           tick_recorder: Optional[TickRecorder] = None,
           record_ticks: bool = True,
           sandbox_stdout: Optional[bool] = None,
       ):
           if tick_recorder is not None:
               self.tick_recorder = tick_recorder
           elif record_ticks:
               self.tick_recorder = TickRecorder(
                   auto_save_csv=logs_csv_path("<strategy>_ticks")
               )
           else:
               self.tick_recorder = None

           if sandbox_stdout is None:
               sandbox_stdout = record_ticks
           self._sandbox_stdout = sandbox_stdout
   ```

4. **One instrumentation line at the end of `run`**:

   ```python
   if self.tick_recorder is not None:
       self.tick_recorder.record_and_emit(
           state, result,
           fair={PRODUCT_A: fair_a, PRODUCT_B: fair_b, ...},
           sandbox_stdout=self._sandbox_stdout,
       )
   return result, 0, trader_data_out
   ```

   - `fair` dict keys **must** match `state.order_depths` product
     symbols so the visualizer overlays the line on the correct chart.
   - Pass `None` (or omit the key) for products where you don't
     compute a fair - the visualizer will just skip the fair line
     on those charts.

### What you get for free

- `LOGS/<strategy>_ticks_<YYYY-MM-DD_HH-MM>.csv` with columns:
  `round, day, timestamp, product, position, quotes_json, fair_json`.
- The backtester's `.log` (under `LOGS/`) contains a `lambdaLog` JSON
  per tick carrying `{t, orders, fair}` - this is what
  `VISUALIZER.parser` reads to drive the fair line on the Order Book
  Viewer page.
- Zero boilerplate inside the strategy beyond the single
  `record_and_emit` call - all JSON shape knowledge lives in
  `MODULES.tick_recorder`.

---

## 2. IMC upload mode - STRIP before submitting

Before pasting `trader.py` into the grader, do all of the following.
It is deliberately mechanical so an agent can run through it:

1. **Delete** `import _repo_path  # ...` (line 1-ish).
2. **Delete** `from MODULES import TickRecorder, logs_csv_path`.
3. **Delete** the `tick_recorder`, `record_ticks`, `sandbox_stdout`
   kwargs from `Trader.__init__`, plus any attributes they assign
   (`self.tick_recorder`, `self._sandbox_stdout`).
4. **Delete** the entire `if self.tick_recorder is not None: ...`
   block in `run`.
5. **Grep for `print(`**. The sandbox tolerates prints but they eat
   runtime budget; remove any you added for debugging.
6. **Drop any imports** that are now unused (e.g. `Optional` if only
   the kwargs used it; `json` usually stays because `traderData` is
   JSON).
7. **Confirm `Trader.run` signature** is exactly
   `def run(self, state: TradingState):` returning
   `return result, conversions, trader_data_out` with
   `conversions: int` and `trader_data_out: str`.
8. **Re-run the backtester** against the stripped file and confirm
   the P&L is unchanged. If it isn't, something functional got caught
   in the strip - undo and try again.

### Why

The TickRecorder is a dev-only instrument:

- It writes files in `__post_init__` (`atexit` hook on the auto-save
  CSV path). The grader's sandbox forbids filesystem writes.
- It uses `sys.stdout.write` to emit the `lambdaLog` JSON. The grader
  doesn't need this (it reads its own internal state), and extra
  stdout is runtime overhead.
- `MODULES` imports the repo root, which doesn't exist on the
  grader's machine.

Keeping the instrumentation confined to a single call site at the
bottom of `run` means the strip is a one-line delete plus a few
imports. Any strategy that follows this pattern is reversibly dev-
vs-submission switchable.

---

## 3. Copy-from template

```python
# ==== DEV / BACKTEST version ====
import _repo_path  # noqa: F401

from datamodel import OrderDepth, TradingState, Order
import json
from typing import Optional

from MODULES import TickRecorder, logs_csv_path

PRODUCT = "MY_PRODUCT"


class Trader:
    def __init__(
        self,
        tick_recorder: Optional[TickRecorder] = None,
        record_ticks: bool = True,
        sandbox_stdout: Optional[bool] = None,
    ):
        if tick_recorder is not None:
            self.tick_recorder = tick_recorder
        elif record_ticks:
            self.tick_recorder = TickRecorder(
                auto_save_csv=logs_csv_path("mystrat_ticks")
            )
        else:
            self.tick_recorder = None
        if sandbox_stdout is None:
            sandbox_stdout = record_ticks
        self._sandbox_stdout = sandbox_stdout

    def run(self, state: TradingState):
        saved = json.loads(state.traderData) if state.traderData else {}

        result = {}
        fairs = {}
        for product, depth in state.order_depths.items():
            if product != PRODUCT:
                result[product] = []
                continue
            fair = self._compute_fair(depth, saved)
            orders = self._make_orders(depth, state.position.get(product, 0), fair)
            result[product] = orders
            fairs[product] = fair

        trader_data_out = json.dumps(saved)

        if self.tick_recorder is not None:
            self.tick_recorder.record_and_emit(
                state, result,
                fair=fairs,
                sandbox_stdout=self._sandbox_stdout,
            )

        return result, 0, trader_data_out

    def _compute_fair(self, depth, saved):
        ...

    def _make_orders(self, depth, position, fair):
        ...


# ==== IMC upload version (after strip) ====
#
# from datamodel import OrderDepth, TradingState, Order
# import json
#
# PRODUCT = "MY_PRODUCT"
#
#
# class Trader:
#     def run(self, state: TradingState):
#         saved = json.loads(state.traderData) if state.traderData else {}
#         result = {}
#         for product, depth in state.order_depths.items():
#             if product != PRODUCT:
#                 result[product] = []
#                 continue
#             fair = self._compute_fair(depth, saved)
#             orders = self._make_orders(depth, state.position.get(product, 0), fair)
#             result[product] = orders
#         return result, 0, json.dumps(saved)
#
#     def _compute_fair(self, depth, saved): ...
#     def _make_orders(self, depth, position, fair): ...
```

See `[ACO_hardcode.py](ACO_hardcode.py)` for a fully fleshed single-product
example and `[primo_final.py](primo_final.py)` for a multi-product one
(IPR + ACO, with `fair` assembled from per-product saved state).

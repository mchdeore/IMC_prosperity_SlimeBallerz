"""
Shared parser for prosperity backtester log files.

The log file has three sections separated by top-level labels:
    Sandbox logs:
    Activities log:
    Trade History:

Sandbox logs: sequence of JSON blocks with `lambdaLog` containing
              our [ORDER] lines (if order_log enabled).
Trade History: JSON list of fill events with trailing commas.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

_TRADE_BLOCK_RE = re.compile(
    r'"timestamp":\s*(?P<ts>\d+),\s*'
    r'"buyer":\s*"(?P<buyer>[^"]*)",\s*'
    r'"seller":\s*"(?P<seller>[^"]*)",\s*'
    r'"symbol":\s*"(?P<symbol>[^"]+)",\s*'
    r'"currency":\s*"(?P<currency>[^"]*)",\s*'
    r'"price":\s*(?P<price>-?[\d.]+),\s*'
    r'"quantity":\s*(?P<qty>\d+)',
    re.MULTILINE,
)

_ORDER_LINE_RE = re.compile(
    r"\[ORDER\] t=(\d+) p=(\w+) phase=(\w+) side=(\w+) "
    r"price=(-?\d+) qty=(\d+) fair=(-?[\d.]+) pos=(-?\d+)"
)


def split_sections(log_path: Path) -> tuple[str, str]:
    """
    Returns (sandbox_text, trade_history_text) from a backtest log file.
    """
    text = log_path.read_text()
    sandbox_start = text.find("Sandbox logs:")
    activities_start = text.find("Activities log:")
    trade_start = text.find("Trade History:")

    if sandbox_start == -1 or trade_start == -1:
        return "", ""

    sandbox = text[sandbox_start:activities_start if activities_start != -1 else trade_start]
    trade_history = text[trade_start:]
    return sandbox, trade_history


def iter_trades(trade_history_text: str) -> Iterator[dict]:
    """Yields dicts with keys: timestamp, buyer, seller, symbol, price, qty."""
    for m in _TRADE_BLOCK_RE.finditer(trade_history_text):
        yield {
            "timestamp": int(m.group("ts")),
            "buyer":     m.group("buyer"),
            "seller":    m.group("seller"),
            "symbol":    m.group("symbol"),
            "price":     float(m.group("price")),
            "qty":       int(m.group("qty")),
        }


def iter_orders(sandbox_text: str) -> Iterator[dict]:
    """
    Yields dicts with keys: timestamp, product (short: ACO/IPR), phase,
    side (B/S), price, qty, fair, pos. Duplicate (ts, product) rows
    are NOT deduped here - caller can dedupe if needed.
    """
    for m in _ORDER_LINE_RE.finditer(sandbox_text):
        yield {
            "timestamp": int(m.group(1)),
            "product":   m.group(2),
            "phase":     m.group(3),
            "side":      m.group(4),
            "price":     int(m.group(5)),
            "qty":       int(m.group(6)),
            "fair":      float(m.group(7)),
            "pos":       int(m.group(8)),
        }


def get_our_fills(trade_history_text: str):
    """
    Returns list of fills where we were the counterparty.
    Each fill: {ts, symbol, price, qty, side ('B' or 'S')}.
    """
    fills = []
    for trade in iter_trades(trade_history_text):
        if trade["buyer"] == "SUBMISSION":
            side = "B"
        elif trade["seller"] == "SUBMISSION":
            side = "S"
        else:
            continue
        fills.append({
            "timestamp": trade["timestamp"],
            "symbol":    trade["symbol"],
            "price":     trade["price"],
            "qty":       trade["qty"],
            "side":      side,
        })
    return fills

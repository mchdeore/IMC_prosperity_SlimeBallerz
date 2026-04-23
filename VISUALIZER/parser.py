"""Parse IMC Prosperity logs and raw market-data CSVs into tidy DataFrames.

Two log layouts are supported automatically:

1. Submission log (what the grading server returns): a single line of JSON with
   keys ``submissionId``, ``sandboxLog`` (list), ``activitiesLog`` (CSV string)
   and ``tradeHistory`` (list).

2. Local backtester log (``prosperity4btest`` / similar): plain-text sections
   separated by the headers ``Sandbox logs:``, ``Activities log:`` and
   ``Trade History:``. The sandbox section is a stream of concatenated
   pretty-printed JSON objects, the activities section is a ``;``-separated
   CSV, and the trade-history section is a JSON array that sometimes has
   trailing commas (we tolerate that).

Raw market data (the ``DATA/`` folder) is also supported through
``load_market_data``; it accepts any ``prices_*.csv`` and pairs it with the
matching ``trades_*.csv`` by filename convention.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Public data container
# ---------------------------------------------------------------------------


@dataclass
class LogBundle:
    """All tidy frames a visualizer needs for a single log or data file."""

    book: pd.DataFrame          # L1-L3 order book + mid + pnl
    trades: pd.DataFrame        # market + own trades; see `source` column
    quotes: pd.DataFrame        # orders the strategy posted (may be empty)
    position: pd.DataFrame      # position per (product, timestamp)
    fair: pd.DataFrame          # optional fair-value series (may be empty)
    meta: dict = field(default_factory=dict)

    @property
    def products(self) -> list[str]:
        return sorted(self.book["product"].dropna().unique().tolist())


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _detect_format(text: str) -> str:
    head = text.lstrip()[:256]
    if head.startswith("{") and '"submissionId"' in head:
        return "submission"
    if head.startswith("Sandbox logs:"):
        return "backtest"
    # Best-effort fallback.
    if "Activities log:" in text:
        return "backtest"
    return "submission"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _loads_lenient(blob: str):
    """``json.loads`` that tolerates trailing commas (seen in backtester logs)."""
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", blob))


def _iter_sandbox_objects(sandbox_text: str):
    """Yield each ``{sandboxLog, lambdaLog, timestamp}`` JSON object.

    The backtester emits them as pretty-printed objects with no separator;
    ``raw_decode`` walks them one at a time without us having to guess
    delimiters.
    """
    decoder = json.JSONDecoder()
    idx = 0
    n = len(sandbox_text)
    while idx < n:
        while idx < n and sandbox_text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        obj, end = decoder.raw_decode(sandbox_text, idx)
        yield obj
        idx = end


def _parse_activities_csv(csv_text: str) -> pd.DataFrame:
    df = pd.read_csv(StringIO(csv_text), sep=";")
    rename = {
        "bid_price_1": "bid1", "bid_volume_1": "bidv1",
        "bid_price_2": "bid2", "bid_volume_2": "bidv2",
        "bid_price_3": "bid3", "bid_volume_3": "bidv3",
        "ask_price_1": "ask1", "ask_volume_1": "askv1",
        "ask_price_2": "ask2", "ask_volume_2": "askv2",
        "ask_price_3": "ask3", "ask_volume_3": "askv3",
        "mid_price": "mid_raw",
        "profit_and_loss": "pnl",
    }
    df = df.rename(columns=rename)
    num_cols = [c for c in df.columns if c not in ("product",)]
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Robust mid: avoids the spikes/gaps you get when one (or both) sides
    # of the book is empty for a tick.
    #   - both sides present  -> (bid + ask) / 2
    #   - only one side       -> that side's best price
    #   - both sides missing  -> forward-fill last good mid
    # We keep the grader-reported number in ``mid_raw`` for reference.
    df = df.sort_values(["product", "timestamp"]).reset_index(drop=True)
    bid1 = df["bid1"] if "bid1" in df.columns else None
    ask1 = df["ask1"] if "ask1" in df.columns else None
    if bid1 is not None and ask1 is not None:
        mid = (bid1 + ask1) / 2.0
        mid = mid.where(mid.notna(), bid1)
        mid = mid.where(mid.notna(), ask1)
    elif bid1 is not None:
        mid = bid1.astype(float)
    elif ask1 is not None:
        mid = ask1.astype(float)
    else:
        mid = pd.Series([float("nan")] * len(df))
    df["mid"] = mid
    df["mid"] = df.groupby("product")["mid"].ffill()
    return df


def _normalize_trades(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(
            columns=["timestamp", "product", "price", "quantity", "source"]
        )
    df = pd.DataFrame(raw)
    df = df.rename(columns={"symbol": "product"})
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")

    buyer = df.get("buyer", "").fillna("")
    seller = df.get("seller", "").fillna("")
    source = []
    for b, s in zip(buyer, seller):
        if b == "SUBMISSION":
            source.append("own_buy")
        elif s == "SUBMISSION":
            source.append("own_sell")
        else:
            source.append("market")
    df["source"] = source
    keep = ["timestamp", "product", "price", "quantity", "source"]
    return df[keep].sort_values(["product", "timestamp"]).reset_index(drop=True)


def _quotes_from_lambda(sandbox_objs) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (quotes_df, fair_df) parsed from lambdaLog payloads."""
    quote_rows: list[dict] = []
    fair_rows: list[dict] = []
    for obj in sandbox_objs:
        payload = obj.get("lambdaLog") or ""
        if not payload:
            continue
        try:
            data = _loads_lenient(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # The backtester writes each sandbox object with an outer
        # ``timestamp`` that carries cross-day offsets (day 0 -> 0-999900,
        # day 1 -> 1,000,000-1,999,900, ...). The embedded ``t`` inside
        # ``lambdaLog`` is the trader's per-day ``state.timestamp`` which
        # restarts at 0 each day. Always prefer the outer timestamp so
        # multi-day logs don't collapse every day's quotes onto day 0.
        ts = obj.get("timestamp")
        if ts is None:
            ts = data.get("t")
        orders = data.get("orders") or {}
        for product, order_list in orders.items():
            for entry in order_list or []:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                price, qty = entry[0], entry[1]
                if qty == 0:
                    continue
                quote_rows.append({
                    "timestamp": ts,
                    "product": product,
                    "price": float(price),
                    "quantity": int(qty),
                    "side": "buy" if qty > 0 else "sell",
                })
        fair = data.get("fair") or {}
        if isinstance(fair, dict):
            for product, val in fair.items():
                try:
                    fair_rows.append({
                        "timestamp": ts,
                        "product": product,
                        "fair": float(val),
                    })
                except (TypeError, ValueError):
                    continue

    quotes = pd.DataFrame(quote_rows, columns=["timestamp", "product", "price", "quantity", "side"])
    fairs = pd.DataFrame(fair_rows, columns=["timestamp", "product", "fair"])
    return quotes, fairs


DAY_TICKS = 1_000_000  # each Prosperity day is 1M ticks; sandbox resets between days


def _position_from_fills(trades: pd.DataFrame) -> pd.DataFrame:
    """Cumulative position per product, reset at every day boundary.

    Each day is an independent sandbox on the grading server, so
    ``state.position`` that the trader sees restarts at 0 at the start of
    every ``DAY_TICKS`` window. Without this reset, a multi-day backtest
    log (`--merge-pnl` / default sequential timestamps) produces a line
    that appears to blow past +/-limit when in reality the trader stayed
    within the cap every day.
    """
    if trades.empty:
        return pd.DataFrame(columns=["timestamp", "product", "position"])
    own = trades[trades["source"].isin(("own_buy", "own_sell"))].copy()
    if own.empty:
        return pd.DataFrame(columns=["timestamp", "product", "position"])
    signed = own["quantity"] * own["source"].map({"own_buy": 1, "own_sell": -1})
    own = own.assign(delta=signed)
    own["day"] = (own["timestamp"] // DAY_TICKS).astype(int)
    own = own.sort_values(["product", "day", "timestamp"])
    own["position"] = own.groupby(["product", "day"])["delta"].cumsum()
    return own[["timestamp", "product", "position"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Format-specific readers
# ---------------------------------------------------------------------------


def _parse_submission_log(text: str) -> LogBundle:
    blob = json.loads(text)
    book = _parse_activities_csv(blob.get("activitiesLog", ""))
    trades = _normalize_trades(blob.get("tradeHistory", []))
    sandbox = blob.get("sandboxLog", []) or []
    quotes, fair = _quotes_from_lambda(sandbox)
    position = _position_from_fills(trades)
    meta = {
        "format": "submission",
        "submissionId": blob.get("submissionId"),
    }
    return LogBundle(book=book, trades=trades, quotes=quotes,
                     position=position, fair=fair, meta=meta)


_SECTION_RE = re.compile(
    r"^(Sandbox logs|Activities log|Trade History):\s*$", re.MULTILINE
)


def _split_sections(text: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def _parse_backtest_log(text: str) -> LogBundle:
    sections = _split_sections(text)
    sandbox = list(_iter_sandbox_objects(sections.get("Sandbox logs", "")))
    book = _parse_activities_csv(sections.get("Activities log", ""))
    raw_trades = _loads_lenient(sections.get("Trade History", "[]") or "[]")
    trades = _normalize_trades(raw_trades)
    quotes, fair = _quotes_from_lambda(sandbox)
    position = _position_from_fills(trades)
    meta = {"format": "backtest"}
    return LogBundle(book=book, trades=trades, quotes=quotes,
                     position=position, fair=fair, meta=meta)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def load_log(path: str | Path) -> LogBundle:
    """Parse a log file and return a :class:`LogBundle`."""
    path = Path(path)
    text = path.read_text()
    fmt = _detect_format(text)
    bundle = (_parse_submission_log if fmt == "submission" else _parse_backtest_log)(text)
    bundle.meta["path"] = str(path)
    bundle.meta["name"] = path.name
    return bundle


def load_market_data(prices_csv: str | Path,
                     trades_csv: Optional[str | Path] = None) -> LogBundle:
    """Load a ``prices_*.csv`` (and optional ``trades_*.csv``) from DATA/.

    The resulting bundle has no quotes/positions (no strategy was run); it is
    useful for inspecting raw market structure alongside a strategy log.
    """
    prices_csv = Path(prices_csv)
    book = _parse_activities_csv(prices_csv.read_text())

    if trades_csv is None:
        guess = prices_csv.with_name(
            prices_csv.name.replace("prices_", "trades_", 1)
        )
        trades_csv = guess if guess.exists() else None

    trades = pd.DataFrame(columns=["timestamp", "product", "price", "quantity", "source"])
    if trades_csv is not None and Path(trades_csv).exists():
        raw = pd.read_csv(Path(trades_csv), sep=";")
        raw = raw.rename(columns={"symbol": "product"})
        raw["source"] = "market"
        trades = raw[["timestamp", "product", "price", "quantity", "source"]].copy()

    empty_quotes = pd.DataFrame(columns=["timestamp", "product", "price", "quantity", "side"])
    empty_position = pd.DataFrame(columns=["timestamp", "product", "position"])
    empty_fair = pd.DataFrame(columns=["timestamp", "product", "fair"])
    meta = {
        "format": "market_data",
        "name": prices_csv.name,
        "path": str(prices_csv),
        "trades_path": str(trades_csv) if trades_csv else None,
    }
    return LogBundle(book=book, trades=trades, quotes=empty_quotes,
                     position=empty_position, fair=empty_fair, meta=meta)


# ---------------------------------------------------------------------------
# File discovery helpers for the app's source picker
# ---------------------------------------------------------------------------


def discover_sources(root: Path) -> list[dict]:
    """Return a list of selectable sources in a repository root."""
    root = Path(root)
    sources: list[dict] = []

    logs_dir = root / "LOGS"
    if logs_dir.exists():
        for p in sorted(logs_dir.glob("*.log")):
            sources.append({
                "label": f"LOG  -  {p.name}",
                "value": f"log::{p}",
            })

    data_dir = root / "DATA"
    if data_dir.exists():
        # Recurse so DATA/round1/, DATA/round2/, etc. are all picked up
        # (the backtester expects a round<N>/ subdir layout, so the raw
        # CSVs typically live one level deep).
        for p in sorted(data_dir.rglob("prices_*.csv")):
            rel = p.relative_to(data_dir)
            pretty = rel.as_posix() if rel != Path(p.name) else p.name
            sources.append({
                "label": f"DATA -  {pretty}",
                "value": f"data::{p}",
            })
    return sources


def load_source(value: str) -> LogBundle:
    kind, _, path = value.partition("::")
    if kind == "log":
        return load_log(path)
    if kind == "data":
        return load_market_data(path)
    raise ValueError(f"Unknown source type: {kind!r}")

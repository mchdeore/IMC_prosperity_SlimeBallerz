# SlimeBallerz Strategy Monitor

A Bloomberg/Fincept-inspired dashboard that parses IMC Prosperity logs and
raw market data and shows, for a selected product:

- Top-of-book (best bid / ask) as step lines with a shaded spread
- Your posted quotes (buy / sell triangles)
- Market trades (grey dots, sized by quantity)
- Your fills (filled green / red triangles)
- Mid and, if emitted by the strategy, fair value
- Position over time as a synced sub-axis with position-limit guide lines
- KPI cards: P&L, current position, fill count, quote count, market trade count

## Data sources

The source dropdown is populated automatically from:

- `LOGS/*.log` - both the grading-server submission JSON logs and the local
  `prosperity4btest` logs are auto-detected.
- `DATA/prices_*.csv` - raw market data (the matching `trades_*.csv` is
  paired by filename). No strategy activity is shown for these files, but
  the same chart layout is reused so you can compare market structure.

## Making quotes show up

Quotes are read from `lambdaLog` payloads shaped like:

```json
{"t": 12300, "orders": {"ASH_COATED_OSMIUM": [[10001, 5], [10012, -6]]}}
```

`primo_final.py` already emits this when `sandbox_stdout=True`. Strategies
that only print to stdout without JSON will still work - the book, market
trades and fills come through, and position is derived from `SUBMISSION`
fills when `lambdaLog` is empty.

Optional extension: include a `"fair"` key (`{"ACO": 10001.0, ...}`) in the
`lambdaLog` object and it will be drawn as a dashed line.

## Install

```bash
pip install -r requirements.txt
```

## Run

From the repository root:

```bash
python -m visualizer.app                                 # auto-discover
python -m visualizer.app LOGS/backtest_2026-04-19_11-24.log
python -m visualizer.app DATA/prices_round_1_day_0.csv
```

Then open <http://127.0.0.1:8050>.

## File map

| File | Purpose |
| --- | --- |
| `parser.py` | Log/CSV -> tidy DataFrames (book, trades, quotes, position, fair) |
| `figures.py` | Plotly figure + KPI builders |
| `app.py` | Dash layout + callbacks + CLI entry point |
| `assets/style.css` | Dark Bloomberg-ish theme |

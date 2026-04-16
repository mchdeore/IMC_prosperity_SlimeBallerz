#!/usr/bin/env bash
# Wrapper for prosperity4btest. Usage from repo root:
#   ./scripts/backtest.sh 0
#   TRADER_SCRIPT=trader_SkewedDelta_PepperRoot_v7.py ./scripts/backtest.sh 1 --merge-pnl
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec prosperity4btest "${TRADER_SCRIPT:-$ROOT/trader.py}" "$@"

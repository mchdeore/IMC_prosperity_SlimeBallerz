"""
Shared modules for strategies (tick logging, helpers, …).

Strategies under ``strageties/`` run with only that directory on ``sys.path``.
Before ``from MODULES import ...``, add the repo root::

    import sys
    from pathlib import Path

    _REPO_ROOT = Path(__file__).resolve().parents[1]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    from MODULES import TickRecorder
"""

from .backtest_source_manifest import write_manifest_next_to_tick_csv
from .tick_recorder import TickRecorder, logs_csv_path

__all__ = ["TickRecorder", "logs_csv_path", "write_manifest_next_to_tick_csv"]

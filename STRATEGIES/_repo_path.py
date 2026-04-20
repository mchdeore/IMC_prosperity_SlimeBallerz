"""
Side-effect import: put the repository root on sys.path.

The Prosperity backtester only adds ``strageties/`` to the path, so
``from MODULES import ...`` would otherwise fail. Use at the top of any
strategy in this folder::

    import _repo_path  # noqa: F401
    from MODULES import TickRecorder
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

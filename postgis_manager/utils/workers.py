"""Keep QThread workers alive until they finish.

Usage: launch(w) instead of w.start()
"""
from __future__ import annotations

_pool: set = set()


def launch(worker) -> None:
    """Start a QThread worker, keeping a Python reference until finished."""
    _pool.add(worker)
    worker.finished.connect(lambda: _pool.discard(worker))
    worker.start()

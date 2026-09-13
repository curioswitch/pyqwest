from __future__ import annotations

from ._util import run_child


# When a call's argument raises, the arguments already evaluated are freed with
# the exception still set. A Drop that calls into Python then sees that
# exception as its own, which crashes CPython, so the test runs
# `_unwinding_child.py` in a fresh interpreter.
def test_dropped_during_exception() -> None:
    run_child("_unwinding_child.py", prints="ok")

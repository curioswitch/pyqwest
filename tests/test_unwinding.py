from __future__ import annotations

import pytest

from ._util import run_child


# When a call's argument raises, the arguments already evaluated are freed with
# the exception still set. A Drop that calls into Python then sees that
# exception as its own, which crashes CPython, so each case runs
# `_unwinding_child.py` in a fresh interpreter.
@pytest.mark.parametrize("library", ["asyncio", "trio"])
def test_dropped_during_exception(library: str) -> None:
    run_child("_unwinding_child.py", library, prints="ok")

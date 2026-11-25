from __future__ import annotations

import importlib.util
import sysconfig

import pytest

from ._util import run_child


@pytest.mark.skipif(
    importlib.util.find_spec("gevent") is None, reason="gevent is not installed"
)
@pytest.mark.skipif(
    bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    reason="gevent does not support free-threaded Python",
)
def test_sync_requests_yield_to_gevent() -> None:
    # gevent patches the process, so the requests run in a child.
    run_child("_gevent_child.py", prints="ok")

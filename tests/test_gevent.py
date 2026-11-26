from __future__ import annotations

import importlib.util
import sysconfig

import pytest

from pyqwest import HTTPVersion

from ._util import run_child

pytestmark = [
    pytest.mark.skipif(
        importlib.util.find_spec("gevent") is None, reason="gevent is not installed"
    ),
    pytest.mark.skipif(
        bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        reason="gevent does not support free-threaded Python",
    ),
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
]


@pytest.mark.parametrize("http_version", ["h1", "h2"], indirect=True)
def test_sync_requests_yield_to_gevent(url: str, http_version: HTTPVersion) -> None:
    # gevent monkey-patches the whole process, so run the client in a child.
    version = "h2" if http_version == HTTPVersion.HTTP2 else "h1"
    run_child("_gevent_child.py", url, version, prints="ok")

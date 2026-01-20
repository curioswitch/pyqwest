from __future__ import annotations

import time

from pyqwest._pyqwest import get_sync_timeout, set_sync_timeout


def test_set_timeout() -> None:
    with set_sync_timeout(5.0):
        timeout = get_sync_timeout()
        assert timeout is not None
        assert 4.0 < timeout.total_seconds() < 5.0
        time.sleep(0.1)
        timeout = get_sync_timeout()
        assert timeout is not None
        assert 3.9 < timeout.total_seconds() < 4.9
    timeout = get_sync_timeout()
    assert timeout is None

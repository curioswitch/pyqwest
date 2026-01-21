from __future__ import annotations

import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from pyqwest import SyncClient


def setup(name: str, repo: str, revision: str) -> None:
    dest = Path(__file__).parent.parent / "out" / "integration" / name
    dest_path = Path(dest)
    # For now, don't try to be incremental since syncing Python version changes
    # wouldn't be trivial.
    if dest_path.exists():
        shutil.rmtree(dest_path)

    dest_path.mkdir(parents=True)

    url = f"https://github.com/{repo}/archive/{revision}.zip"

    response = SyncClient().get(url)
    if response.status != 200:
        msg = f"Failed to download {url}: {response.status} {response.text()}"
        raise RuntimeError(msg)

    with ZipFile(BytesIO(response.content)) as zf:
        for member in zf.infolist():
            # strip the top-level directory
            filename = member.filename.split("/", 1)[1]
            dest = dest_path / filename
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.write_bytes(zf.read(member))

    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    if not hasattr(sys, "_is_gil_enabled") or sys._is_gil_enabled():  # noqa: SLF001
        py += "+gil"
    else:
        py += "t"
    subprocess.run(
        ["uv", "sync", "--python", py, "--directory", str(dest_path)], check=True
    )

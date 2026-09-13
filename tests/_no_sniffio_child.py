"""Runs an asyncio request with sniffio unimportable, for `test_transport`.

Takes the URL as an argument and prints the response status.
"""

from __future__ import annotations

import asyncio
import sys

# A None entry makes `import sniffio` raise ModuleNotFoundError.
sys.modules["sniffio"] = None  # ty: ignore[invalid-assignment]

from pyqwest import Request, get_default_transport  # noqa: E402


async def main(url: str) -> None:
    res = await get_default_transport().execute(Request("GET", url))
    async for _ in res.content:
        pass
    print(res.status)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

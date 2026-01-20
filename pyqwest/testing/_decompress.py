from __future__ import annotations

import zlib
from typing import Protocol

from pyqwest._pyqwest import _BrotliDecompressor, _ZstdDecompressor


def get_decompressor(encoding: str | None) -> Decompressor:
    match encoding:
        case "br":
            return _BrotliDecompressor()
        case "gzip":
            return GZipDecompressor()
        case "zstd":
            return _ZstdDecompressor()
        case _:
            return IdentityDecompressor()


class Decompressor(Protocol):
    def feed(self, data: bytes, *, end: bool) -> bytes: ...


class GZipDecompressor:
    def __init__(self) -> None:
        self._decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)

    def feed(self, data: bytes, *, end: bool) -> bytes:
        decompressed = self._decompressor.decompress(data)
        if end:
            decompressed += self._decompressor.flush()
        return decompressed


class IdentityDecompressor:
    def feed(self, data: bytes, *, end: bool) -> bytes:
        return data

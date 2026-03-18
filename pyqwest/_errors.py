from __future__ import annotations

from enum import IntEnum


class StreamErrorCode(IntEnum):
    """Error codes for HTTP/2 stream errors."""

    NO_ERROR = 0
    PROTOCOL_ERROR = 1
    INTERNAL_ERROR = 2
    FLOW_CONTROL_ERROR = 3
    SETTINGS_TIMEOUT = 4
    STREAM_CLOSED = 5
    FRAME_SIZE_ERROR = 6
    REFUSED_STREAM = 7
    CANCEL = 8
    COMPRESSION_ERROR = 9
    CONNECT_ERROR = 10
    ENHANCE_YOUR_CALM = 11
    INADEQUATE_SECURITY = 12
    HTTP_1_1_REQUIRED = 13

    @classmethod
    def _missing_(cls, _value: int) -> StreamErrorCode:
        return cls.INTERNAL_ERROR


class StreamError(Exception):
    """An error representing an HTTP/2+ stream error."""

    code: StreamErrorCode
    """The error code associated with the stream error."""

    def __init__(self, message: str, code: StreamErrorCode | int) -> None:
        """Creates a new StreamError.

        Args:
            message: The error message.
            code: The stream error code.
        """
        super().__init__(message)
        if isinstance(code, int):
            code = StreamErrorCode(code)
        self.code = code

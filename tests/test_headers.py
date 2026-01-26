from __future__ import annotations

import pytest

from pyqwest import Headers, HTTPHeaderName


def test_headers_no_duplicates() -> None:
    h = Headers()
    # Confirm these are views by reusing throughout the test.
    keys = h.keys()
    values = h.values()
    items = h.items()

    assert len(h) == 0
    assert ("foo", "bar") not in items
    assert len(items) == 0
    assert list(items) == []
    assert "foo" not in h
    assert len(keys) == 0
    assert list(keys) == []
    assert "foo" not in keys
    assert 10 not in h
    assert 10 not in keys
    assert len(values) == 0
    assert "foo" not in values
    assert list(values) == []
    with pytest.raises(KeyError):
        _ = h["missing"]
    with pytest.raises(KeyError):
        del h["missing"]
    assert h.get("missing") is None
    assert h.get(10) is None  # pyright: ignore[reportArgumentType]
    assert h.get("missing", "default") == "default"
    assert h.getall("missing") == []
    assert repr(h) == "Headers()"

    h["Content-Type"] = "application/json"
    h["X-Test"] = "foo"
    assert repr(h) == "Headers(('content-type', 'application/json'), ('x-test', 'foo'))"
    assert h["content-type"] == "application/json"
    assert h.setdefault("content-type", "text/plain") == "application/json"
    assert h["CONTENT-TYPE"] == "application/json"
    assert h.getall("content-type") == ["application/json"]
    assert h.getall("X-Test") == ["foo"]
    assert h["X-Test"] == "foo"
    assert h.get("x-test") == "foo"
    assert "content-type" in h
    assert "CONTENT-TYPE" in h
    assert "x-test" in h
    assert "x-test" in keys
    assert len(keys) == 2
    assert len(h) == 2
    assert ("x-test", "foo") in items
    assert (10, "foo") not in items
    assert ("x-test", 10) not in items
    assert ("x-test",) not in items  # pyright: ignore[reportOperatorIssue]
    assert "foo" in values
    assert "bar" not in values
    assert 10 not in values
    assert list(items) == [("content-type", "application/json"), ("x-test", "foo")]
    assert h == h
    assert h == Headers({"Content-Type": "application/json", "X-Test": "foo"})
    assert h == {("content-type", "application/json"), ("x-test", "foo")}
    assert h == [("Content-Type", "application/json"), ("X-Test", "foo")]
    assert h != [("Content-Type", "application/json")]
    assert h != {("content-type", "application/json"), ("x-test", "bar")}
    assert h != [
        ("Content-Type", "application/json"),
        ("X-Test", "foo"),
        ("X-Test2", "bar"),
    ]
    assert list(keys) == ["content-type", "x-test"]
    assert list(h) == ["content-type", "x-test"]
    assert list(values) == ["application/json", "foo"]
    h["content-type"] = "text/plain"
    assert h["Content-Type"] == "text/plain"
    assert list(items) == [("content-type", "text/plain"), ("x-test", "foo")]
    assert list(keys) == ["content-type", "x-test"]
    assert list(values) == ["text/plain", "foo"]
    del h["CONTENT-TYPE"]
    assert "content-type" not in h
    assert len(h) == 1
    h.clear()
    assert len(h) == 0
    assert list(items) == []
    assert h.setdefault("new-header", "new-value") == "new-value"
    assert h["new-header"] == "new-value"
    assert h.setdefault("another-header") is None
    with pytest.raises(KeyError):
        _ = h["another-header"]

    with pytest.raises(ValueError, match="Invalid header name"):
        h["日本語"] = "value"

    with pytest.raises(ValueError, match="Invalid header value"):
        h["value"] = "\x00value"


def test_headers_duplicates() -> None:
    h = Headers()
    keys = h.keys()
    values = h.values()
    items = h.items()

    h.add("X-Test", "foo")
    h.add("X-Test", "bar")
    assert len(h) == 1
    assert h["x-test"] == "foo"
    assert h.getall("x-test") == ["foo", "bar"]
    assert list(keys) == ["x-test"]
    assert list(h) == ["x-test"]
    assert list(values) == ["foo", "bar"]
    assert list(items) == [("x-test", "foo"), ("x-test", "bar")]
    assert ("x-test", "foo") in items
    assert ("x-test", "bar") in items
    assert ("x-test", "baz") not in items
    assert "foo" in values
    assert "bar" in values
    assert "baz" not in values
    h.add("X-Test", "baz")
    assert len(h) == 1
    assert list(keys) == ["x-test"]
    assert list(values) == ["foo", "bar", "baz"]
    assert list(items) == [("x-test", "foo"), ("x-test", "bar"), ("x-test", "baz")]
    assert h.getall("x-test") == ["foo", "bar", "baz"]
    h["authorization"] = "cookie"
    assert h["authorization"] == "cookie"
    assert list(keys) == ["x-test", "authorization"]
    assert list(values) == ["foo", "bar", "baz", "cookie"]
    assert list(items) == [
        ("x-test", "foo"),
        ("x-test", "bar"),
        ("x-test", "baz"),
        ("authorization", "cookie"),
    ]
    assert h == [
        ("x-test", "foo"),
        ("x-test", "bar"),
        ("x-test", "baz"),
        ("authorization", "cookie"),
    ]
    assert h != [("x-test", "foo"), ("x-test", "baz"), ("authorization", "cookie")]
    del h["x-test"]
    assert "x-test" not in h
    assert len(h) == 1
    h["x-Test"] = "again"
    assert h["x-test"] == "again"
    assert list(items) == [("authorization", "cookie"), ("x-test", "again")]
    h.add("x-test", "and again")
    h.pop("x-test", None)
    h.pop("x-test", "bar")
    with pytest.raises(KeyError):
        h.pop("x-test")
    with pytest.raises(TypeError):
        h.pop("x-test", None, "foo")  # pyright: ignore[reportCallIssue]
    assert list(items) == [("authorization", "cookie")]
    h.add("x-animal", "bear")
    h.add("x-animal", "cat")
    h.update({"x-animal": "dog"}, plant="cactus")
    assert list(items) == [
        ("authorization", "cookie"),
        ("x-animal", "dog"),
        ("plant", "cactus"),
    ]
    assert h.getall("x-animal") == ["dog"]
    h.update(fruit="banana")
    assert h["fruit"] == "banana"
    h.update({"fruit": "orange"})
    assert h["fruit"] == "orange"
    h.update([("x-animal", "elephant"), ("x-animal", "fox")], fruit="apple")
    assert list(items) == [
        ("authorization", "cookie"),
        ("x-animal", "fox"),
        ("plant", "cactus"),
        ("fruit", "apple"),
    ]
    h.add("x-animal", "elephant")
    assert sorted(
        [h.popitem(), h.popitem(), h.popitem(), h.popitem(), h.popitem()]
    ) == [
        ("authorization", "cookie"),
        ("fruit", "apple"),
        ("plant", "cactus"),
        ("x-animal", "elephant"),
        ("x-animal", "fox"),
    ]
    with pytest.raises(KeyError):
        h.popitem()


def test_headers_all_names() -> None:
    # We have custom logic to memoize header names for efficiency, so go through them all
    # to verify the memoization isn't incorrect.
    header_cases = [
        ("accept", "accept"),
        ("accept-charset", "accept-charset"),
        ("accept-encoding", "accept-encoding"),
        ("accept-language", "accept-language"),
        ("accept-ranges", "accept-ranges"),
        ("access-control-allow-credentials", "access-control-allow-credentials"),
        ("access-control-allow-headers", "access-control-allow-headers"),
        ("access-control-allow-methods", "access-control-allow-methods"),
        ("access-control-allow-origin", "access-control-allow-origin"),
        ("access-control-expose-headers", "access-control-expose-headers"),
        ("access-control-max-age", "access-control-max-age"),
        ("access-control-request-headers", "access-control-request-headers"),
        ("access-control-request-method", "access-control-request-method"),
        ("age", "age"),
        ("allow", "allow"),
        ("alt-svc", "alt-svc"),
        ("authorization", "authorization"),
        ("cache-control", "cache-control"),
        ("cache-status", "cache-status"),
        ("cdn-cache-control", "cdn-cache-control"),
        ("connection", "connection"),
        ("content-disposition", "content-disposition"),
        ("content-encoding", "content-encoding"),
        ("content-language", "content-language"),
        ("content-length", "content-length"),
        ("content-location", "content-location"),
        ("content-range", "content-range"),
        ("content-security-policy", "content-security-policy"),
        ("content-security-policy-report-only", "content-security-policy-report-only"),
        ("content-type", "content-type"),
        ("cookie", "cookie"),
        ("dnt", "dnt"),
        ("date", "date"),
        ("etag", "etag"),
        ("expect", "expect"),
        ("expires", "expires"),
        ("forwarded", "forwarded"),
        ("from", "from"),
        ("host", "host"),
        ("if-match", "if-match"),
        ("if-modified-since", "if-modified-since"),
        ("if-none-match", "if-none-match"),
        ("if-range", "if-range"),
        ("if-unmodified-since", "if-unmodified-since"),
        ("last-modified", "last-modified"),
        ("link", "link"),
        ("location", "location"),
        ("max-forwards", "max-forwards"),
        ("origin", "origin"),
        ("pragma", "pragma"),
        ("proxy-authenticate", "proxy-authenticate"),
        ("proxy-authorization", "proxy-authorization"),
        ("public-key-pins", "public-key-pins"),
        ("public-key-pins-report-only", "public-key-pins-report-only"),
        ("range", "range"),
        ("referer", "referer"),
        ("referrer-policy", "referrer-policy"),
        ("refresh", "refresh"),
        ("retry-after", "retry-after"),
        ("sec-websocket-accept", "sec-websocket-accept"),
        ("sec-websocket-extensions", "sec-websocket-extensions"),
        ("sec-websocket-key", "sec-websocket-key"),
        ("sec-websocket-protocol", "sec-websocket-protocol"),
        ("sec-websocket-version", "sec-websocket-version"),
        ("server", "server"),
        ("set-cookie", "set-cookie"),
        ("strict-transport-security", "strict-transport-security"),
        ("te", "te"),
        ("trailer", "trailer"),
        ("transfer-encoding", "transfer-encoding"),
        ("user-agent", "user-agent"),
        ("upgrade", "upgrade"),
        ("upgrade-insecure-requests", "upgrade-insecure-requests"),
        ("vary", "vary"),
        ("via", "via"),
        ("warning", "warning"),
        ("www-authenticate", "www-authenticate"),
        ("x-content-type-options", "x-content-type-options"),
        ("x-dns-prefetch-control", "x-dns-prefetch-control"),
        ("x-frame-options", "x-frame-options"),
        ("x-xss-protection", "x-xss-protection"),
        ("x-pyvoy", "x-pyvoy"),
    ]
    h = Headers(header_cases)
    assert sorted(h) == sorted(name for name, _ in header_cases)


def test_headers_http_header_name() -> None:
    h = Headers()
    # Confirm these are views by reusing throughout the test.
    keys = h.keys()
    values = h.values()
    items = h.items()

    assert len(h) == 0
    assert (HTTPHeaderName("foo"), "bar") not in items
    assert len(items) == 0
    assert list(items) == []
    assert HTTPHeaderName("foo") not in h
    assert len(keys) == 0
    assert list(keys) == []
    assert HTTPHeaderName("foo") not in keys
    assert 10 not in h
    assert 10 not in keys
    assert len(values) == 0
    assert "foo" not in values
    assert list(values) == []
    with pytest.raises(KeyError):
        _ = h[HTTPHeaderName("missing")]
    with pytest.raises(KeyError):
        del h[HTTPHeaderName("missing")]
    assert h.get(HTTPHeaderName("missing")) is None
    assert h.get(10) is None  # pyright: ignore[reportArgumentType]
    assert h.get(HTTPHeaderName("missing"), "default") == "default"
    assert h.getall(HTTPHeaderName("missing")) == []
    assert repr(h) == "Headers()"

    h[HTTPHeaderName.CONTENT_TYPE] = "application/json"
    h[HTTPHeaderName("X-Test")] = "foo"
    assert repr(h) == "Headers(('content-type', 'application/json'), ('x-test', 'foo'))"
    assert h[HTTPHeaderName.CONTENT_TYPE] == "application/json"
    assert h.setdefault(HTTPHeaderName.CONTENT_TYPE, "text/plain") == "application/json"
    assert h[HTTPHeaderName.CONTENT_TYPE] == "application/json"
    assert h.getall(HTTPHeaderName.CONTENT_TYPE) == ["application/json"]
    assert h.getall(HTTPHeaderName("X-Test")) == ["foo"]
    assert h[HTTPHeaderName("X-Test")] == "foo"
    assert h.get(HTTPHeaderName("x-test")) == "foo"
    assert HTTPHeaderName("content-type") in h
    assert HTTPHeaderName("CONTENT-TYPE") in h
    assert HTTPHeaderName("x-test") in h
    assert HTTPHeaderName("x-test") in keys
    assert len(keys) == 2
    assert len(h) == 2
    assert (HTTPHeaderName("x-test"), "foo") in items
    assert (10, "foo") not in items
    assert (HTTPHeaderName("x-test"), 10) not in items
    assert (HTTPHeaderName("x-test"),) not in items  # pyright: ignore[reportOperatorIssue]
    assert "foo" in values
    assert "bar" not in values
    assert 10 not in values
    assert list(items) == [
        (HTTPHeaderName.CONTENT_TYPE, "application/json"),
        (HTTPHeaderName("x-test"), "foo"),
    ]
    assert h == h
    assert h == Headers(
        {
            HTTPHeaderName.CONTENT_TYPE: "application/json",
            HTTPHeaderName("X-Test"): "foo",
        }
    )
    assert h == {(HTTPHeaderName.CONTENT_TYPE, "application/json"), ("x-test", "foo")}
    assert h == [
        (HTTPHeaderName.CONTENT_TYPE, "application/json"),
        (HTTPHeaderName("X-Test"), "foo"),
    ]
    assert h != [(HTTPHeaderName.CONTENT_TYPE, "application/json")]
    assert h != {
        (HTTPHeaderName.CONTENT_TYPE, "application/json"),
        (HTTPHeaderName("x-test"), "bar"),
    }
    assert h != [
        (HTTPHeaderName.CONTENT_TYPE, "application/json"),
        (HTTPHeaderName("X-Test"), "foo"),
        (HTTPHeaderName("X-Test2"), "bar"),
    ]
    assert list(keys) == [HTTPHeaderName.CONTENT_TYPE, HTTPHeaderName("x-test")]
    assert list(h) == [HTTPHeaderName.CONTENT_TYPE, HTTPHeaderName("x-test")]
    assert list(values) == ["application/json", "foo"]
    h[HTTPHeaderName.CONTENT_TYPE] = "text/plain"
    assert h[HTTPHeaderName.CONTENT_TYPE] == "text/plain"
    assert list(items) == [
        (HTTPHeaderName.CONTENT_TYPE, "text/plain"),
        (HTTPHeaderName("x-test"), "foo"),
    ]
    assert list(keys) == [HTTPHeaderName.CONTENT_TYPE, HTTPHeaderName("x-test")]
    assert list(values) == ["text/plain", "foo"]
    del h[HTTPHeaderName.CONTENT_TYPE]
    assert HTTPHeaderName.CONTENT_TYPE not in h
    assert len(h) == 1
    h.clear()
    assert len(h) == 0
    assert list(items) == []
    assert h.setdefault(HTTPHeaderName("new-header"), "new-value") == "new-value"
    assert h[HTTPHeaderName("new-header")] == "new-value"
    assert h.setdefault(HTTPHeaderName("another-header")) is None
    with pytest.raises(KeyError):
        _ = h[HTTPHeaderName("another-header")]
    with pytest.raises(ValueError, match="Invalid header name"):
        h[HTTPHeaderName("日本語")] = "value"

    with pytest.raises(ValueError, match="Invalid header value"):
        h[HTTPHeaderName("value")] = "\x00value"

    assert HTTPHeaderName.ACCEPT != 10


def test_all_headers() -> None:
    h = Headers(
        {
            HTTPHeaderName.ACCEPT: "accept",
            HTTPHeaderName.ACCEPT_CHARSET: "accept-charset",
            HTTPHeaderName.ACCEPT_ENCODING: "accept-encoding",
            HTTPHeaderName.ACCEPT_LANGUAGE: "accept-language",
            HTTPHeaderName.ACCEPT_RANGES: "accept-ranges",
            HTTPHeaderName.ACCESS_CONTROL_ALLOW_CREDENTIALS: "access-control-allow-credentials",
            HTTPHeaderName.ACCESS_CONTROL_ALLOW_HEADERS: "access-control-allow-headers",
            HTTPHeaderName.ACCESS_CONTROL_ALLOW_METHODS: "access-control-allow-methods",
            HTTPHeaderName.ACCESS_CONTROL_ALLOW_ORIGIN: "access-control-allow-origin",
            HTTPHeaderName.ACCESS_CONTROL_EXPOSE_HEADERS: "access-control-expose-headers",
            HTTPHeaderName.ACCESS_CONTROL_MAX_AGE: "access-control-max-age",
            HTTPHeaderName.ACCESS_CONTROL_REQUEST_HEADERS: "access-control-request-headers",
            HTTPHeaderName.ACCESS_CONTROL_REQUEST_METHOD: "access-control-request-method",
            HTTPHeaderName.AGE: "age",
            HTTPHeaderName.ALLOW: "allow",
            HTTPHeaderName.ALT_SVC: "alt-svc",
            HTTPHeaderName.AUTHORIZATION: "authorization",
            HTTPHeaderName.CACHE_CONTROL: "cache-control",
            HTTPHeaderName.CACHE_STATUS: "cache-status",
            HTTPHeaderName.CDN_CACHE_CONTROL: "cdn-cache-control",
            HTTPHeaderName.CONNECTION: "connection",
            HTTPHeaderName.CONTENT_DISPOSITION: "content-disposition",
            HTTPHeaderName.CONTENT_ENCODING: "content-encoding",
            HTTPHeaderName.CONTENT_LANGUAGE: "content-language",
            HTTPHeaderName.CONTENT_LENGTH: "content-length",
            HTTPHeaderName.CONTENT_LOCATION: "content-location",
            HTTPHeaderName.CONTENT_RANGE: "content-range",
            HTTPHeaderName.CONTENT_SECURITY_POLICY: "content-security-policy",
            HTTPHeaderName.CONTENT_SECURITY_POLICY_REPORT_ONLY: "content-security-policy-report-only",
            HTTPHeaderName.CONTENT_TYPE: "content-type",
            HTTPHeaderName.COOKIE: "cookie",
            HTTPHeaderName.DNT: "dnt",
            HTTPHeaderName.DATE: "date",
            HTTPHeaderName.ETAG: "etag",
            HTTPHeaderName.EXPECT: "expect",
            HTTPHeaderName.EXPIRES: "expires",
            HTTPHeaderName.FORWARDED: "forwarded",
            HTTPHeaderName.FROM: "from",
            HTTPHeaderName.HOST: "host",
            HTTPHeaderName.IF_MATCH: "if-match",
            HTTPHeaderName.IF_MODIFIED_SINCE: "if-modified-since",
            HTTPHeaderName.IF_NONE_MATCH: "if-none-match",
            HTTPHeaderName.IF_RANGE: "if-range",
            HTTPHeaderName.IF_UNMODIFIED_SINCE: "if-unmodified-since",
            HTTPHeaderName.LAST_MODIFIED: "last-modified",
            HTTPHeaderName.LINK: "link",
            HTTPHeaderName.LOCATION: "location",
            HTTPHeaderName.MAX_FORWARDS: "max-forwards",
            HTTPHeaderName.ORIGIN: "origin",
            HTTPHeaderName.PRAGMA: "pragma",
            HTTPHeaderName.PROXY_AUTHENTICATE: "proxy-authenticate",
            HTTPHeaderName.PROXY_AUTHORIZATION: "proxy-authorization",
            HTTPHeaderName.PUBLIC_KEY_PINS: "public-key-pins",
            HTTPHeaderName.PUBLIC_KEY_PINS_REPORT_ONLY: "public-key-pins-report-only",
            HTTPHeaderName.RANGE: "range",
            HTTPHeaderName.REFERER: "referer",
            HTTPHeaderName.REFERRER_POLICY: "referrer-policy",
            HTTPHeaderName.REFRESH: "refresh",
            HTTPHeaderName.RETRY_AFTER: "retry-after",
            HTTPHeaderName.SEC_WEBSOCKET_ACCEPT: "sec-websocket-accept",
            HTTPHeaderName.SEC_WEBSOCKET_EXTENSIONS: "sec-websocket-extensions",
            HTTPHeaderName.SEC_WEBSOCKET_KEY: "sec-websocket-key",
            HTTPHeaderName.SEC_WEBSOCKET_PROTOCOL: "sec-websocket-protocol",
            HTTPHeaderName.SEC_WEBSOCKET_VERSION: "sec-websocket-version",
            HTTPHeaderName.SERVER: "server",
            HTTPHeaderName.SET_COOKIE: "set-cookie",
            HTTPHeaderName.STRICT_TRANSPORT_SECURITY: "strict-transport-security",
            HTTPHeaderName.TE: "te",
            HTTPHeaderName.TRAILER: "trailer",
            HTTPHeaderName.TRANSFER_ENCODING: "transfer-encoding",
            HTTPHeaderName.USER_AGENT: "user-agent",
            HTTPHeaderName.UPGRADE: "upgrade",
            HTTPHeaderName.UPGRADE_INSECURE_REQUESTS: "upgrade-insecure-requests",
            HTTPHeaderName.VARY: "vary",
            HTTPHeaderName.VIA: "via",
            HTTPHeaderName.WARNING: "warning",
            HTTPHeaderName.WWW_AUTHENTICATE: "www-authenticate",
            HTTPHeaderName.X_CONTENT_TYPE_OPTIONS: "x-content-type-options",
            HTTPHeaderName.X_DNS_PREFETCH_CONTROL: "x-dns-prefetch-control",
            HTTPHeaderName.X_FRAME_OPTIONS: "x-frame-options",
            HTTPHeaderName.X_XSS_PROTECTION: "x-xss-protection",
        }
    )
    enum_attrs = [v for v in dir(HTTPHeaderName) if not v.startswith("_")]
    assert len(enum_attrs) == len(h)
    assert dict(h.items()) == {
        "accept": "accept",
        "accept-charset": "accept-charset",
        "accept-encoding": "accept-encoding",
        "accept-language": "accept-language",
        "accept-ranges": "accept-ranges",
        "access-control-allow-credentials": "access-control-allow-credentials",
        "access-control-allow-headers": "access-control-allow-headers",
        "access-control-allow-methods": "access-control-allow-methods",
        "access-control-allow-origin": "access-control-allow-origin",
        "access-control-expose-headers": "access-control-expose-headers",
        "access-control-max-age": "access-control-max-age",
        "access-control-request-headers": "access-control-request-headers",
        "access-control-request-method": "access-control-request-method",
        "age": "age",
        "allow": "allow",
        "alt-svc": "alt-svc",
        "authorization": "authorization",
        "cache-control": "cache-control",
        "cache-status": "cache-status",
        "cdn-cache-control": "cdn-cache-control",
        "connection": "connection",
        "content-disposition": "content-disposition",
        "content-encoding": "content-encoding",
        "content-language": "content-language",
        "content-length": "content-length",
        "content-location": "content-location",
        "content-range": "content-range",
        "content-security-policy": "content-security-policy",
        "content-security-policy-report-only": "content-security-policy-report-only",
        "content-type": "content-type",
        "cookie": "cookie",
        "dnt": "dnt",
        "date": "date",
        "etag": "etag",
        "expect": "expect",
        "expires": "expires",
        "forwarded": "forwarded",
        "from": "from",
        "host": "host",
        "if-match": "if-match",
        "if-modified-since": "if-modified-since",
        "if-none-match": "if-none-match",
        "if-range": "if-range",
        "if-unmodified-since": "if-unmodified-since",
        "last-modified": "last-modified",
        "link": "link",
        "location": "location",
        "max-forwards": "max-forwards",
        "origin": "origin",
        "pragma": "pragma",
        "proxy-authenticate": "proxy-authenticate",
        "proxy-authorization": "proxy-authorization",
        "public-key-pins": "public-key-pins",
        "public-key-pins-report-only": "public-key-pins-report-only",
        "range": "range",
        "referer": "referer",
        "referrer-policy": "referrer-policy",
        "refresh": "refresh",
        "retry-after": "retry-after",
        "sec-websocket-accept": "sec-websocket-accept",
        "sec-websocket-extensions": "sec-websocket-extensions",
        "sec-websocket-key": "sec-websocket-key",
        "sec-websocket-protocol": "sec-websocket-protocol",
        "sec-websocket-version": "sec-websocket-version",
        "server": "server",
        "set-cookie": "set-cookie",
        "strict-transport-security": "strict-transport-security",
        "te": "te",
        "trailer": "trailer",
        "transfer-encoding": "transfer-encoding",
        "user-agent": "user-agent",
        "upgrade": "upgrade",
        "upgrade-insecure-requests": "upgrade-insecure-requests",
        "vary": "vary",
        "via": "via",
        "warning": "warning",
        "www-authenticate": "www-authenticate",
        "x-content-type-options": "x-content-type-options",
        "x-dns-prefetch-control": "x-dns-prefetch-control",
        "x-frame-options": "x-frame-options",
        "x-xss-protection": "x-xss-protection",
    }

    assert str(HTTPHeaderName.CONTENT_TYPE) == "content-type"
    assert repr(HTTPHeaderName.CONTENT_TYPE) == "HTTPHeaderName('content-type')"

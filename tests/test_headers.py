from __future__ import annotations

import pytest

from pyqwest import Headers


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
    assert len(values) == 0
    assert "foo" not in values
    assert list(values) == []
    with pytest.raises(KeyError):
        _ = h["missing"]
    assert h.get("missing") is None
    assert h.get("missing", "default") == "default"
    assert h.getall("missing") == []

    h["Content-Type"] = "application/json"
    h["X-Test"] = "foo"
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
    assert h == Headers({"Content-Type": "application/json", "X-Test": "foo"})
    assert h == {("content-type", "application/json"), ("x-test", "foo")}
    assert h == [("Content-Type", "application/json"), ("X-Test", "foo")]
    assert h != [("Content-Type", "application/json")]
    assert h != [
        ("Content-Type", "application/json"),
        ("X-Test", "foo"),
        ("X-Test2", "bar"),
    ]
    assert list(keys) == ["content-type", "x-test"]
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
    with pytest.raises(KeyError):
        h.pop("x-test")
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

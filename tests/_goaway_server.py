"""A pyvoy server that retires every HTTP/2 connection after one request."""

from __future__ import annotations

import json
import re
import urllib.request

from pyvoy import PyvoyServer

DRAIN_TIMEOUT = 0.05
"""How long Envoy waits between its GOAWAY notice and the final GOAWAY."""


class GoawayTestServer(PyvoyServer):
    """Serves `tests.apps.asgi.goaway` with Envoy's maximum requests per
    connection set to one, so every HTTP/2 connection is retired as soon as a
    request arrives on it: Envoy sends a GOAWAY notice naming stream 2^31-1,
    then after `DRAIN_TIMEOUT` the final GOAWAY naming the highest stream it
    has received."""

    def __init__(self) -> None:
        super().__init__("tests.apps.asgi.goaway", lifespan=False)
        self._connections_at_reset = 0

    def get_envoy_config(self) -> dict:
        config = super().get_envoy_config()
        listener = config["static_resources"]["listeners"][0]
        http = listener["filter_chains"][0]["filters"][0]["typed_config"]
        http["common_http_protocol_options"] = {"max_requests_per_connection": 1}
        http["drain_timeout"] = f"{DRAIN_TIMEOUT}s"
        return config

    @property
    def port(self) -> int:
        return self.listener_port

    def connections(self) -> int:
        """HTTP/2 connections Envoy accepted since `reset`."""
        return self._total_connections() - self._connections_at_reset

    def seen(self) -> list[tuple[str, str, str]]:
        """The (method, path, content) of every request Envoy dispatched to
        the app since `reset`, oldest first."""
        return [tuple(entry) for entry in json.loads(self._app_request("/seen"))]

    def release(self) -> None:
        """Finishes the open `/held` responses."""
        self._app_request("/release")

    def reset(self) -> None:
        """Forgets the requests seen and connections accepted so far, and
        holds `/held` responses again."""
        self._app_request("/reset")
        self._connections_at_reset = self._total_connections()

    def _total_connections(self) -> int:
        return self._stat("http.ingress_http.downstream_cx_http2_total")

    def _app_request(self, path: str) -> bytes:
        # HTTP/1, so it never counts toward the HTTP/2 connections under test.
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as response:
            return response.read()

    def _admin(self, path: str) -> bytes:
        with urllib.request.urlopen(f"http://{self._admin_address}{path}") as response:
            return response.read()

    def _stat(self, name: str) -> int:
        pattern = f"^{re.escape(name)}$"
        data = json.loads(self._admin(f"/stats?format=json&filter={pattern}"))
        return next(stat["value"] for stat in data["stats"] if stat["name"] == name)

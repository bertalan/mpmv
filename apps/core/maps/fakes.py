"""`FakeTileUpstream`: answers from memory and records every call (EVT.14)."""

from collections.abc import Mapping

from . import TileUpstreamError, UpstreamTile
from .engine import PNG_MAGIC


class FakeTileUpstream:
    def __init__(
        self,
        *,
        body: bytes = PNG_MAGIC + b"fake-tile",
        headers: Mapping[str, str] | None = None,
        error: TileUpstreamError | None = None,
    ) -> None:
        self.body = body
        self.headers = dict(headers or {})
        self.error = error
        self.calls: list[tuple[str, str, float]] = []

    def fetch(self, url: str, *, user_agent: str, timeout: float) -> UpstreamTile:
        self.calls.append((url, user_agent, timeout))
        if self.error is not None:
            raise self.error
        return UpstreamTile(body=self.body, headers=self.headers)

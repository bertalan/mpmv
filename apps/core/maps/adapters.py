"""The HTTP upstream of the tile proxy (EVT.14)."""

import urllib.error
import urllib.request
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

from . import TileNotAvailable, TileUpstream, TileUpstreamError, TileUpstreamTimeout, UpstreamTile

MAX_TILE_BYTES = 512 * 1024
_HEADERS = ("Cache-Control", "Expires")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is an upstream error: never follow it (also not to http:// or another host)."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _extra_handlers() -> list[urllib.request.BaseHandler]:
    return []


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect(), *_extra_handlers())


class HttpTileUpstream:
    """GET over HTTPS with the identifying User-Agent. Errors name only the failure: the URL may
    carry a provider key, so it never appears in a message."""

    def fetch(self, url: str, *, user_agent: str, timeout: float) -> UpstreamTile:
        request = urllib.request.Request(  # noqa: S310 (https template from settings, validated)
            url, headers={"User-Agent": user_agent, "Accept": "image/png"}, method="GET"
        )
        try:
            with _opener().open(request, timeout=timeout) as response:
                body = response.read(MAX_TILE_BYTES + 1)
                headers = {n: response.headers[n] for n in _HEADERS if n in response.headers}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise TileNotAvailable("HTTP 404") from None
            raise TileUpstreamError(f"HTTP {exc.code}") from None
        except OSError as exc:  # URLError, connection errors, timeouts
            reason = getattr(exc, "reason", None)
            if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
                raise TileUpstreamTimeout("timed out") from None
            raise TileUpstreamError(type(exc).__name__) from None
        if len(body) > MAX_TILE_BYTES:
            raise TileUpstreamError("tile too large")
        return UpstreamTile(body=body, headers=headers)


class RefuseNetworkUpstream:
    """The upstream of the test settings: no test may reach a real tile server."""

    def fetch(self, url: str, *, user_agent: str, timeout: float) -> UpstreamTile:
        raise TileUpstreamError("network disabled in tests: use FakeTileUpstream")


def default_upstream() -> TileUpstream:
    upstream: TileUpstream = import_string(settings.MAP_TILE_UPSTREAM)()
    return upstream

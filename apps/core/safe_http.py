"""MIG.12 slice 1: an SSRF-guarded outbound HTTP client for URLs a person or a feed chose.

`safe_get` is for any URL that staff, vendors or a connector entered (spec 13 §13.2, SEC.9).
Fixed provider URLs keep using `apps.core.outbound`. The guard:

* the URL is checked first (`check_url`): scheme allow-list (HTTPS by default), no userinfo, no
  control characters or backslashes, only ports 443 (and 80 when `http` is allowed), no IPv4
  written as decimal, octal or hex, no zone ids, IDN converted to punycode;
* the host is resolved here, every answer must be a global address (`is_blocked_ip`: loopback,
  private, link-local, CGNAT, multicast, reserved, unspecified, cloud metadata, IPv4-mapped and
  NAT64 or 6to4 wrappers of those are refused);
* the connection goes to the vetted IP itself, with the original host name for SNI, certificate
  and `Host` (no second lookup, so DNS rebinding cannot swap the address);
* redirects are refused unless `max_redirects` > 0; each hop repeats every check;
* the body is read in chunks and refused as soon as it passes `max_bytes`.

The transport and the resolver are small interfaces so tests never touch the network.
"""

import http.client
import ipaddress
import re
import socket
import ssl
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlsplit

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

DEFAULT_PORTS = {"https": 443, "http": 80}
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK = 16 * 1024
_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20\x7f\\]|[^\x00-\x7f]")
_LEGACY_IPV4 = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)

_METADATA = frozenset(
    ipaddress.ip_address(a)
    for a in ("169.254.169.254", "169.254.170.2", "100.100.100.200", "fd00:ec2::254", "192.0.0.192")
)
_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "100.64.0.0/10",  # CGNAT
        "64:ff9b::/96",  # NAT64: carries any IPv4 address
        "64:ff9b:1::/48",
        "2002::/16",  # 6to4: carries any IPv4 address
        "2001::/32",  # Teredo
        "fec0::/10",  # deprecated site-local
        "::/96",  # deprecated IPv4-compatible
    )
)


class SafeHttpError(Exception):
    """Base of every error `safe_get` raises. Messages never carry the query or credentials."""


class UnsafeUrl(SafeHttpError):
    """The URL itself is refused (scheme, userinfo, port, odd host spelling)."""


class BlockedAddress(SafeHttpError):
    """The host resolves to (or is) an address that must not be reached."""


class ResolutionFailed(SafeHttpError):
    """The host did not resolve."""


class TooManyRedirects(SafeHttpError):
    """A redirect was refused: not allowed, over the cap, or without a usable Location."""


class ResponseTooLarge(SafeHttpError):
    """The body is longer than `max_bytes`."""


class TransportFailure(SafeHttpError):
    """Connection, TLS or timeout error."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str  # lower-case, ASCII (punycode), no trailing dot, no brackets
    port: int
    path: str  # path plus query, never empty

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}"


def _legacy_ipv4(host: str) -> bool:
    """True for a host `inet_aton` would read as an address although it is not a dotted quad."""
    if not _LEGACY_IPV4.match(host) and not host.replace(".", "").isdigit():
        return False
    try:
        socket.inet_aton(host)
    except OSError:
        return host.rsplit(".", 1)[-1].isdigit()  # "1.2.3.999": still numeric, never a name
    return True


def check_url(url: str, *, allowed_schemes: Sequence[str] = ("https",)) -> Target:
    """Validate `url` and split it; raises `UnsafeUrl`. No network access."""
    if _FORBIDDEN_CHARS.search(url):
        raise UnsafeUrl("URL contains a control character, space, backslash or non-ASCII text")
    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise UnsafeUrl("URL is malformed") from None
    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes or scheme not in DEFAULT_PORTS:
        raise UnsafeUrl("URL scheme is not allowed")
    if "@" in parts.netloc:
        raise UnsafeUrl("URL carries credentials")
    if not hostname:
        raise UnsafeUrl("URL has no host")
    allowed_ports = {DEFAULT_PORTS[s] for s in allowed_schemes if s in DEFAULT_PORTS}
    if port is not None and (port not in allowed_ports or port != DEFAULT_PORTS[scheme]):
        raise UnsafeUrl("URL port is not allowed")
    host = hostname.lower().rstrip(".")
    if not host or ".." in host or "%" in host:
        raise UnsafeUrl("URL host is malformed")
    try:
        address: IPAddress | None = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None:
        if _legacy_ipv4(host):
            raise UnsafeUrl("URL host is an IPv4 address in a non-standard spelling")
        if host == "localhost" or host.endswith(".localhost"):
            raise BlockedAddress("localhost is refused")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise UnsafeUrl("URL host is not a valid name") from None
    elif is_blocked_ip(address):
        raise BlockedAddress("address is not publicly routable")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return Target(scheme, host, port or DEFAULT_PORTS[scheme], path)


def is_blocked_ip(address: IPAddress | str) -> bool:
    """True when the address must not be reached from the server."""
    ip = ipaddress.ip_address(address) if isinstance(address, str) else address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return True
    if ip in _METADATA:
        return True
    if any(ip.version == net.version and ip in net for net in _EXTRA_BLOCKED):
        return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


type Resolver = Callable[[str, int], Sequence[str]]


def system_resolver(host: str, port: int) -> Sequence[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ResolutionFailed("host did not resolve") from None
    return [str(info[4][0]) for info in infos]


def vet_host(target: Target, resolver: Resolver) -> str:
    """Resolve `target.host`; refuse if ANY answer is blocked; return the IP to connect to."""
    try:
        literal = ipaddress.ip_address(target.host)
    except ValueError:
        addresses = list(resolver(target.host, target.port))
    else:
        addresses = [str(literal)]
    if not addresses:
        raise ResolutionFailed("host did not resolve")
    parsed: list[IPAddress] = []
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            raise BlockedAddress("resolver returned a non-address") from None
        if is_blocked_ip(ip):
            raise BlockedAddress("host resolves to an address that is not publicly routable")
        parsed.append(ip)
    return str(parsed[0])


@dataclass
class RawResponse:
    """What a transport returns: status, headers and a chunk iterator the caller must close."""

    status: int
    headers: Mapping[str, str]
    chunks: Callable[[], Iterator[bytes]]
    close: Callable[[], None] = lambda: None


class Transport(Protocol):
    def get(
        self,
        target: Target,
        ip: str,
        headers: Mapping[str, str],
        timeouts: tuple[float, float],
    ) -> RawResponse: ...


class _PinnedConnection(http.client.HTTPConnection):
    """Connects to `pinned_ip`; `host` stays the name (Host header, SNI, certificate)."""

    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def _open_socket(self) -> socket.socket:
        return socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)

    def connect(self) -> None:
        self.sock = self._open_socket()


class _PinnedTLSConnection(_PinnedConnection):
    def connect(self) -> None:
        raw = self._open_socket()
        context = ssl.create_default_context()
        try:
            self.sock = context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class HttpClientTransport:
    """The real transport: `http.client`, pinned to the vetted IP, never follows redirects."""

    def get(
        self,
        target: Target,
        ip: str,
        headers: Mapping[str, str],
        timeouts: tuple[float, float],
    ) -> RawResponse:
        connect_timeout, read_timeout = timeouts
        cls = _PinnedTLSConnection if target.scheme == "https" else _PinnedConnection
        conn = cls(target.host, target.port, ip, connect_timeout)
        try:
            conn.request("GET", target.path, headers=dict(headers))
            if conn.sock is not None:
                conn.sock.settimeout(read_timeout)
            response = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise TransportFailure(f"request failed ({type(exc).__name__})") from None

        def chunks() -> Iterator[bytes]:
            while chunk := response.read(_CHUNK):
                yield chunk

        return RawResponse(response.status, dict(response.getheaders()), chunks, conn.close)


@dataclass(frozen=True)
class SafeResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url_origin: str  # scheme and host of the final hop, never the path or query
    redirects: int = 0
    resolved_ip: str = field(default="", repr=False)


def safe_get(
    url: str,
    *,
    timeout: tuple[float, float] = (5.0, 15.0),
    max_bytes: int,
    allowed_schemes: Sequence[str] = ("https",),
    max_redirects: int = 0,
    headers: Mapping[str, str] | None = None,
    resolver: Resolver = system_resolver,
    transport: Transport | None = None,
) -> SafeResponse:
    """GET `url` through the SSRF guard. `timeout` is (connect, read) seconds.

    A 3xx answer raises `TooManyRedirects` unless `max_redirects` > 0, and then every hop is
    checked like the first. Non-2xx statuses are returned, not raised: the caller decides.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    transport = transport or HttpClientTransport()
    sent = {"User-Agent": "mpmv-fetch", "Accept-Encoding": "identity", **(headers or {})}
    current = url
    for hop in range(max_redirects + 1):
        target = check_url(current, allowed_schemes=allowed_schemes)
        ip = vet_host(target, resolver)
        raw = transport.get(target, ip, sent, timeout)
        try:
            if raw.status in REDIRECT_STATUSES:
                location = raw.headers.get("Location") or raw.headers.get("location")
                if not location or hop >= max_redirects:
                    raise TooManyRedirects("redirect refused")
                current = urljoin(current, location)
                continue
            declared = raw.headers.get("Content-Length") or raw.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ResponseTooLarge("declared length is over the limit")
            body = bytearray()
            for chunk in raw.chunks():
                body += chunk
                if len(body) > max_bytes:
                    raise ResponseTooLarge("body is over the limit")
            return SafeResponse(raw.status, dict(raw.headers), bytes(body), target.origin, hop, ip)
        finally:
            raw.close()
    raise TooManyRedirects("redirect refused")  # pragma: no cover

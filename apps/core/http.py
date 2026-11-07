"""Small request helpers shared by the HTTP adapters of every app."""

import ipaddress
from functools import lru_cache

from django.conf import settings
from django.http import HttpRequest

type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def trusted_proxy_networks(entries: tuple[str, ...]) -> tuple[IPNetwork, ...]:
    """Parse `TRUSTED_PROXIES` (addresses or CIDR networks); raises ValueError on a bad entry."""
    return tuple(ipaddress.ip_network(entry.strip(), strict=False) for entry in entries)


def _parse_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _is_trusted_proxy(remote_addr: str) -> bool:
    address = _parse_ip(remote_addr)
    if address is None:
        return False
    try:
        networks = trusted_proxy_networks(tuple(settings.TRUSTED_PROXIES))
    except ValueError:  # reported by the core.E002 system check
        return False
    ip = ipaddress.ip_address(address)
    return any(ip in network for network in networks)


def client_ip(request: HttpRequest) -> str | None:
    """The client address: the trusted reverse proxy's header, otherwise `REMOTE_ADDR`.

    `TRUSTED_PROXY_HEADER` (e.g. `X-Real-IP`, which Caddy overwrites, see deploy/caddy) is read
    only when `REMOTE_ADDR` belongs to `TRUSTED_PROXIES`; anybody else could send any value. For
    a list such as `X-Forwarded-For` only the right-most hop counts: the proxy appended it, the
    earlier ones came from the client. A missing or garbled header falls back to `REMOTE_ADDR`.
    """
    remote = request.META.get("REMOTE_ADDR")
    remote_addr = str(remote) if remote else None
    header = settings.TRUSTED_PROXY_HEADER
    if header and remote_addr and _is_trusted_proxy(remote_addr):
        forwarded = request.headers.get(header, "")
        if forwarded.strip() and (address := _parse_ip(forwarded.rsplit(",", 1)[-1])):
            return address
    return remote_addr

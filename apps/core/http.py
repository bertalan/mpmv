"""Small request helpers shared by the HTTP adapters of every app."""

from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str | None:
    """The client address, from `REMOTE_ADDR`.

    Behind the reverse proxy the deployment must set it from the trusted proxy header (OPS);
    otherwise every client looks like the proxy and shares one throttle.
    """
    ip = request.META.get("REMOTE_ADDR")
    return str(ip) if ip else None

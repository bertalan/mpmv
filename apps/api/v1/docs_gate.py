"""Who may read the API documentation (CORE.29, owner decision Q-13).

Locally and on STAGE `API_DOCS_PUBLIC` is true and the Swagger page and `openapi.json` are open.
On LIVE it is false: a valid API key (any scope) or a signed-in staff member gets them, anybody
else gets a plain 404, as if the URL did not exist. A refused key counts against the per-IP
limit of CORE.20, like a refused key on any operation.
"""

import functools
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponseBase

from apps.api.auth import authenticate_request, note_refusal, refused_key_block


def _is_staff(request: HttpRequest) -> bool:
    user = getattr(request, "user", None)
    return bool(user is not None and user.is_authenticated and user.is_active and user.is_staff)


def _has_valid_key(request: HttpRequest) -> bool:
    if not request.headers.get("Authorization"):
        return False  # nothing to check, and nothing to count
    if refused_key_block(request) is not None:
        return False
    result = authenticate_request(request, None)
    if not result.ok:
        note_refusal(request, result)
    return result.ok


def restrict_docs[V: Callable[..., HttpResponseBase]](view: V) -> V:
    """`docs_decorator` of the API: the gate described in the module docstring."""

    @functools.wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not settings.API_DOCS_PUBLIC and not (_is_staff(request) or _has_valid_key(request)):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped  # type: ignore[return-value]

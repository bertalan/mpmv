"""The "Find coordinates" endpoint (EVT.12): a thin adapter over the geocoder chain.

Called by the map picker of the Wagtail admin and the vendor area (EVT.13), never by visitors:
only staff and vendor members may use it, each within a per-user limit (10 lookups a minute by
default). POST with CSRF, so another site cannot spend a user's lookups.
"""

import datetime as dt
import math

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.translation import get_language
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.services import consume_rate_limit

from . import AddressQuery
from . import services as geo

MAX_FIELD_LENGTH = 200
FIELDS = ("country", "postcode", "city", "street")


def _language(request: HttpRequest) -> str:
    known = {code for code, _ in settings.LANGUAGES}
    wanted = request.POST.get("language", "").strip().lower()
    if wanted in known:
        return wanted
    current = (get_language() or settings.LANGUAGE_CODE).lower()
    return current if current in known else str(settings.LANGUAGE_CODE)


@never_cache
@require_POST
def find_coordinates(request: HttpRequest) -> JsonResponse:
    if not geo.may_geocode(request.user):
        return JsonResponse({"detail": "Only staff and vendor members can geocode."}, status=403)
    values = {field: request.POST.get(field, "").strip() for field in FIELDS}
    if any(len(value) > MAX_FIELD_LENGTH for value in values.values()):
        return JsonResponse({"detail": "Address field too long."}, status=400)
    query = AddressQuery(**values)
    if query.is_empty():
        return JsonResponse({"detail": "Type a street, a postcode or a city."}, status=400)
    retry_after = consume_rate_limit(
        f"geocode:user:{request.user.pk}",
        limit=settings.GEOCODING_USER_LIMIT,
        window=dt.timedelta(seconds=settings.GEOCODING_USER_WINDOW),
    )
    if retry_after:
        response = JsonResponse({"detail": "Too many lookups, try again shortly."}, status=429)
        response["Retry-After"] = str(max(1, math.ceil(retry_after.total_seconds())))
        return response
    outcome = geo.geocode_address(query, language=_language(request))
    return JsonResponse(
        {
            "candidates": [result.to_dict() for result in outcome.results],
            "provider": outcome.provider,
            "cached": outcome.cached,
            "attempts": [{"provider": a.provider, "status": a.status} for a in outcome.attempts],
        }
    )

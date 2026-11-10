"""Account views: thin adapters over `services`."""

import datetime as dt
import math

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from apps.core.http import client_ip
from apps.core.i18n import configured_languages, translate_path
from apps.core.services import consume_rate_limit

from .services import set_preferred_language
from .session_language import SESSION_LANGUAGE_KEY

SWITCH_LIMIT = 30  # per client address and minute (SEC.13): the anonymous POST writes a session
SWITCH_WINDOW = dt.timedelta(minutes=1)


@require_POST
def switch_language(request: HttpRequest) -> HttpResponse:
    """Language switcher: redirect to the current page in the chosen language (I18N.1).

    The URL prefix decides the language, so no cookie is set; a signed-in user's choice is also
    stored as `preferred_language`, an anonymous visitor's in the session. Unknown languages and
    off-site `next` URLs are ignored.
    """
    retry_after = consume_rate_limit(
        f"switch_language:ip:{client_ip(request) or 'unknown'}",
        limit=SWITCH_LIMIT,
        window=SWITCH_WINDOW,
    )
    if retry_after:
        response = HttpResponse("Too many requests, try again shortly.", status=429)
        response["Retry-After"] = str(max(1, math.ceil(retry_after.total_seconds())))
        return response
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = "/"
    language = request.POST.get("language", "")
    if language not in configured_languages():
        return HttpResponseRedirect(next_url)
    if request.user.is_authenticated:
        set_preferred_language(request.user, language)
    else:  # an anonymous choice lives in the session (no cookie): unprefixed pages read it
        if request.session.get(SESSION_LANGUAGE_KEY) != language:  # no write when unchanged
            request.session[SESSION_LANGUAGE_KEY] = language
    exact = request.POST.get("exact") == "1"
    if exact and request.POST.get("notice") == "1":
        # The object has no translation: the visitor lands on the language's home, told once.
        with translation.override(language):
            messages.info(request, gettext("This content is not available in this language."))
    if exact:
        return HttpResponseRedirect(next_url)  # already the target in `language`
    return HttpResponseRedirect(translate_path(next_url, language))

"""Account views: thin adapters over `services`."""

import datetime as dt
import math

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from apps.core.http import client_ip
from apps.core.i18n import configured_languages, translate_path
from apps.core.public_forms import Protection, public_form
from apps.core.services import consume_rate_limit

from .services import set_preferred_language

# A visitor changes language a few times; 30 a minute per IP is generous (SEC.13).
SWITCH_LIMIT = 30
SWITCH_WINDOW = dt.timedelta(minutes=1)


@public_form(
    protection=Protection.ALLOW,
    purpose="Language switcher (redirect to the same page in another language)",
    task="I18N.1",
    reason="stores nothing for an anonymous visitor and redirects only to this host",
    rate_limit="per IP, generous",
)
@require_POST
def switch_language(request: HttpRequest) -> HttpResponseBase:
    """Language switcher: redirect to the current page in the chosen language (I18N.1).

    The URL prefix decides the language, so no cookie is set; a signed-in user's choice is also
    stored as `preferred_language`. Unknown languages and off-site `next` URLs are ignored.
    """
    wait = consume_rate_limit(
        f"language_switch:ip:{client_ip(request)}", limit=SWITCH_LIMIT, window=SWITCH_WINDOW
    )
    if wait:
        retry = str(max(1, math.ceil(wait.total_seconds())))
        return HttpResponse("Too many requests.", status=429, headers={"Retry-After": retry})
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
    exact = request.POST.get("exact") == "1"
    if exact and request.POST.get("notice") == "1":
        # The object has no translation: the visitor lands on the language's home, told once.
        with translation.override(language):
            messages.info(request, gettext("This content is not available in this language."))
    if exact:
        return HttpResponseRedirect(next_url)  # already the target in `language`
    return HttpResponseRedirect(translate_path(next_url, language))

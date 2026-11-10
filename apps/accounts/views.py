"""Account views: thin adapters over `services`."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponseRedirect
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from apps.core.i18n import configured_languages, translate_path
from apps.core.public_forms import Protection, public_form

from .services import set_preferred_language


@public_form(
    protection=Protection.ALLOW,
    purpose="Language switcher (redirect to the same page in another language)",
    task="I18N.1",
    reason="stores nothing for an anonymous visitor and redirects only to this host",
    no_rate_limit="a redirect that does no work for a stranger",
)
@require_POST
def switch_language(request: HttpRequest) -> HttpResponseRedirect:
    """Language switcher: redirect to the current page in the chosen language (I18N.1).

    The URL prefix decides the language, so no cookie is set; a signed-in user's choice is also
    stored as `preferred_language`. Unknown languages and off-site `next` URLs are ignored.
    """
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

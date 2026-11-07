"""Account views: thin adapters over `services`."""

from django.http import HttpRequest, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.i18n import configured_languages, translate_path

from .models import User
from .services import set_preferred_language


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
    if isinstance(request.user, User):  # signed in; anonymous visitors are AnonymousUser
        set_preferred_language(request.user, language)
    return HttpResponseRedirect(translate_path(next_url, language))

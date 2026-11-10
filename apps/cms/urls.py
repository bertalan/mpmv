"""Public CMS routes, mounted inside `i18n_patterns` (CMS.1, I18N.1).

`home` keeps its URL name for the rest of the code (redirects, links): it is the language's
home page in the Wagtail tree. Every other path is looked up in the tree by Wagtail.
"""

from django.urls import include, path
from wagtail import urls as wagtail_urls

from apps.core.public_forms import FormSpec, Kind, Protection, declare_public_form

from . import views

# Wagtail's page view restriction forms (a page shared with a password) are public POSTs that no
# code of ours protects yet: the sweep found them (SEC.13). Declared as known gaps so the register
# and `manage.py check` (core.W701) keep saying so until a rate limit exists or the feature stays
# unused; see the SEC.13 pull request.
_RESTRICTION_GAP = "page view restrictions are not rate limited or captcha protected (SEC.13)"
for _name, _purpose in (
    ("wagtailcore_authenticate_with_password", "Enter the password of a restricted page"),
    ("wagtailcore_login", "Sign in to see a login-restricted page"),
):
    declare_public_form(
        _name,
        FormSpec(
            Kind.PUBLIC,
            _purpose,
            "SEC.13",
            Protection.ALLOW,
            reason="Wagtail's own view, unchanged",
            known_gap=_RESTRICTION_GAP,
        ),
    )

urlpatterns = [
    path("", views.home, name="home"),
    path("", include(wagtail_urls)),
]

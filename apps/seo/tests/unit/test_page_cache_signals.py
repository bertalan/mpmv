"""SEO.6: the cache signals are connected per content model, never to every model."""

from django.db.models.signals import post_delete, post_save
from wagtail.models import Locale, Page, PageViewRestriction, Site

from apps.core.models import AuditLog


def test_content_models_have_the_receivers_and_unrelated_models_do_not() -> None:
    for model in (Site, Page, Locale, PageViewRestriction):
        assert post_save.has_listeners(model)
        assert post_delete.has_listeners(model)
    # A receiver without a sender would disable Django's fast delete for every model.
    assert not post_delete.has_listeners(AuditLog)
    assert not post_save.has_listeners(AuditLog)

"""The SEO settings of each Wagtail Site (SEO.8 2/2, spec 10, owner decision D-35) and the
consent log (SEO.5, D-10).

The consent log is the proof that a visitor made a choice. One row per decision, never changed
afterwards: a later change of mind is a new row with the same consent id. A row holds what the
law asks of a proof (when, what was granted, which banner text) and nothing that identifies the
visitor: no IP address, no user agent, no account. The consent id is random, made for one browser
and kept in its cookie.
"""

from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.models import Site

from . import engine


class SeoSettings(BaseSiteSetting):  # type: ignore[misc]  # Wagtail is untyped
    """One row per Wagtail Site. Written only through `services.set_site_indexing` (audited).

    `allow_indexing` is off until staff turn it on, so a new site is noindex. It can only add
    noindex on top of the environment floor (`MPMV_FORCE_NOINDEX`, demo mode): under the floor
    it can be stored off, never on. Do not read it with Wagtail's `for_site`, which creates the
    row on first access, outside the audit trail: use `current`.
    """

    # Editable (Wagtail's is not), so the panel can pick the site.
    site = models.OneToOneField(Site, on_delete=models.CASCADE, verbose_name=_("site"))
    allow_indexing = models.BooleanField(
        _("allow search engines to index this site"),
        default=False,
        help_text=_(
            "Off (the default): search engines are told not to index any page of this site. "
            "On: pages can appear in search results. It has no effect while the server "
            "forces noindex (STAGE or demo)."
        ),
    )

    class Meta:
        verbose_name = _("SEO settings")
        verbose_name_plural = _("SEO settings")

    def __str__(self) -> str:
        return f"{self.site.hostname}: indexing {'on' if self.allow_indexing else 'off'}"

    @classmethod
    def current(cls, site: Site) -> "SeoSettings":
        """The settings of `site`, or unsaved defaults: a read never creates the row."""
        found: SeoSettings | None = cls.objects.filter(site=site).first()
        return found if found is not None else cls(site=site)


class ConsentLog(models.Model):
    ACTION_CHOICES = [(a, a) for a in engine.ACTIONS]

    consent_id = models.UUIDField(db_index=True)
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    granted = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    banner_version = models.CharField(max_length=20)
    provider = models.CharField(max_length=40)
    language = models.CharField(max_length=8, blank=True)
    decided_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-decided_at", "-id"]

    def __str__(self) -> str:
        return f"{self.consent_id} {self.action} {self.decided_at:%Y-%m-%d}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("A consent log row is never changed; add a new one.")
        super().save(*args, **kwargs)

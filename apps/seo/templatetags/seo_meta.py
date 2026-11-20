"""`{% page_social_meta %}`: Open Graph and Twitter card tags of a CMS page (SEO.8 2/2).

Printed by the base layout for every page that has a `page` in its context. The values come from
`meta_engine.resolve_meta`: the page's SEO title and search description, then its summary, and
the image from the page, the site's default image or the operator logo. A page never prints a
second `og:` set; noindex does not change them (they are not a robots signal).
"""

from urllib.parse import urljoin

from django import template
from django.template.context import Context
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from wagtail.models import Site

from apps.core.images import rendition_or_original
from apps.core.services import NoPlatformOperator, current_operator
from apps.seo import meta_engine
from apps.seo.models import SeoSettings

register = template.Library()
OG_IMAGE_FILTER = "width-1200"


def _image_url(image: object, base: str) -> str:
    if image is None or not getattr(image, "file", None):
        return ""
    try:
        return urljoin(f"{base}/", rendition_or_original(image, OG_IMAGE_FILTER)[0])
    except ValueError:  # no file behind the field
        return ""


def page_meta(context: Context) -> meta_engine.PageMeta | None:
    page = context.get("page")
    if page is None or not hasattr(page, "seo_title"):
        return None
    site: Site | None = page.get_site()
    base = site.root_url if site is not None else ""
    settings = SeoSettings.current(site) if site is not None else None
    try:
        logo = current_operator().logo
    except NoPlatformOperator:  # a fresh install: no logo to fall back to
        logo = None
    return meta_engine.resolve_meta(
        page_title=str(page.title),
        seo_title=str(page.seo_title or ""),
        search_description=str(page.search_description or ""),
        summary=str(getattr(page, "summary", "") or ""),
        page_seo_image=_image_url(getattr(page, "seo_image", None), base),
        page_image=_image_url(getattr(page, "image", None), base),
        site_image=_image_url(getattr(settings, "default_image", None), base),
        operator_image=_image_url(logo, base),
    )


@register.simple_tag(takes_context=True)
def page_social_meta(context: Context) -> SafeString | str:
    meta = page_meta(context)
    if meta is None:
        return ""
    tags = [("og:type", "website"), ("og:title", meta.title)]
    if meta.description:
        tags.append(("og:description", meta.description))
    if meta.image:
        tags.append(("og:image", meta.image))
    card = "summary_large_image" if meta.image else "summary"
    html = format_html_join("\n  ", '<meta property="{}" content="{}">', tags)
    return format_html('{}\n  <meta name="twitter:card" content="{}">', html, card)


@register.simple_tag(takes_context=True)
def page_description_meta(context: Context) -> SafeString | str:
    meta = page_meta(context)
    if meta is None or not meta.description:
        return ""
    return format_html('<meta name="description" content="{}">', meta.description)

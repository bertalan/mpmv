"""The blocks of CMS page bodies (I18N.3, CMS.2, spec 10 §10.2), translated with wagtail-localize.

wagtail-localize turns a block's text fields into translatable segments and its image chooser
into an overridable segment: alt texts and captions are translated (the EN text stays until a
translation is published) and a translator can replace an image in one locale only. A gallery
also applies the D-18 replacement rule (`engine.gallery_for_language`).

CMS.2 adds the hero, the call to action, the FAQ and the "featured links" cards. Their templates
are minimal, semantic and CSP-safe (no inline script or style); the `base` theme restyles them
(THM.3, THM.4). A link points to a live page of the site, followed into the reader's language,
or to an external URL. The FAQs of a page give one FAQPage (`cms_tags.faq_jsonld`).

Cards (part 2/2) point to a page, a catalog taxonomy term or a vendor storefront, chosen in the
admin (`choosers`), never typed. A card whose target is not public is left out: a page that is
not live, a term (or its taxonomy or parent terms) without published texts, a vendor that is not
active. Term and storefront URLs follow spec 03 (`engine.term_path`, `engine.storefront_path`).

The launch countdown (CMS.9) announces a launch: the target is entered in the operator's time
zone and stored in UTC, `engine.countdown_state` decides between the countdown and the expired
state, and its links default to the chosen product or category. The theme component renders
it; the theme's `countdown.js` module adds the live counter.

The product grid (CMS.10) shows the newest products of one category, term, brand or vendor with
the CAT.8 listing query (`apps.catalog.services.product_grid`), as the base theme's product
tiles (THM.4), and an optional "See all" link to that listing page, named after the listing for
screen readers. An empty heading falls back to the listing's name, so the section always has
one. Only published products of active vendors show; a grid with none is left out.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.blocks import StructBlockValidationError
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Page

from apps.catalog.models import Category, Product, TaxonomyTranslation, Term, TermTranslation
from apps.catalog.services import (
    ListingScope,
    category_url,
    listed_products,
    product_cards,
    product_grid,
)
from apps.core import translations
from apps.core.money import CURRENCY
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import has_public_storefront

from .choosers import (
    brand_chooser,
    category_chooser,
    product_chooser,
    term_chooser,
    vendor_chooser,
)
from .engine import (
    NEUTRAL,
    PRODUCT_GRID_DEFAULT,
    PRODUCT_GRID_MAX,
    countdown_state,
    gallery_for_language,
    grid_limit,
    link_language,
    storefront_path,
    term_path,
)

# Editors may use headings, emphasis, lists and links; no raw HTML, images or embeds yet.
RICH_TEXT_FEATURES = ["h2", "h3", "bold", "italic", "ol", "ul", "link"]


def language_choices() -> list[tuple[str, Any]]:
    """Neutral first, then every configured language; read at runtime, so no migration."""
    return [(NEUTRAL, _("All languages (neutral)")), *settings.LANGUAGES]


class FigureBlock(blocks.StructBlock):  # type: ignore[misc]  # Wagtail is untyped
    """One image with its alt text and an optional caption."""

    image = ImageChooserBlock()
    alt_text = blocks.CharBlock(
        max_length=250, help_text=_("Describes the image for people who cannot see it.")
    )
    caption = blocks.CharBlock(max_length=250, required=False)

    class Meta:
        icon = "image"
        label = _("Image")
        template = "cms/blocks/figure.html"


class GalleryImageBlock(FigureBlock):
    """A gallery image, neutral or dedicated to one language (D-18)."""

    language = blocks.ChoiceBlock(
        choices=language_choices,
        required=False,
        help_text=_("Shown instead of the neutral images in its language."),
    )


class GalleryBlock(blocks.StructBlock):  # type: ignore[misc]
    """Images shown by the D-18 rule: a language's dedicated images, otherwise the neutral."""

    images = blocks.ListBlock(GalleryImageBlock())

    class Meta:
        icon = "image"
        label = _("Gallery")
        template = "cms/blocks/gallery.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        context["images"] = gallery_for_language(value["images"], _page_language(parent_context))
        return context


def live_page(page: Page | None) -> Page | None:
    """`page` in the active language when that translation is live, else `page` itself when it
    is live, else None: a link to a draft, unpublished or expired page is dropped."""
    if page is None:
        return None
    target: Page = page.specific.localized
    return target if target.live else None


def _page_language(parent_context: dict[str, Any] | None) -> str:
    page = (parent_context or {}).get("page")
    return str(page.locale.language_code if page is not None else get_language())


class LinkValue(blocks.StructValue):  # type: ignore[misc]
    def href(self) -> str:
        """The live page in the active language (or its source), or the URL; "" for no link."""
        if self.get("page") is not None:
            page = live_page(self.get("page"))
            url: str | None = page.url if page is not None else None
            return url or ""
        return str(self.get("url") or "")


class LinkBlock(blocks.StructBlock):  # type: ignore[misc]
    """A labelled link to a page of the site or to an external URL, never both."""

    page = blocks.PageChooserBlock(required=False)
    url = blocks.URLBlock(required=False, label=_("URL"))
    label = blocks.CharBlock(max_length=100)

    class Meta:
        icon = "link"
        label = _("Link")
        value_class = LinkValue

    def clean(self, value: LinkValue) -> LinkValue:
        value = super().clean(value)
        if (value.get("page") is None) == (not value.get("url")):
            raise StructBlockValidationError(
                non_block_errors=[ValidationError(_("Choose a page or enter a URL, not both."))]
            )
        return value


class HeroBlock(blocks.StructBlock):  # type: ignore[misc]
    """The opening banner of a page: a heading, a short text, an image and up to two links."""

    heading = blocks.CharBlock(max_length=120)
    text = blocks.TextBlock(required=False, max_length=400)
    image = ImageChooserBlock(required=False)
    alt_text = blocks.CharBlock(
        required=False,
        max_length=250,
        help_text=_("Describes the image for people who cannot see it."),
    )
    links = blocks.ListBlock(LinkBlock(), min_num=0, max_num=2, default=[])

    class Meta:
        icon = "pick"
        label = _("Hero")
        template = "cms/blocks/hero.html"
        # No caption area: a credited image goes to the page's "Image credits" (CMS.8).
        credit_in_disclosure = True

    def clean(self, value: blocks.StructValue) -> blocks.StructValue:
        value = super().clean(value)
        if value.get("image") is not None and not (value.get("alt_text") or "").strip():
            raise StructBlockValidationError(
                block_errors={"alt_text": ValidationError(_("Describe the image."))}
            )
        return value


class CallToActionBlock(blocks.StructBlock):  # type: ignore[misc]
    """A heading, a short text and one link that invites the reader to act."""

    heading = blocks.CharBlock(max_length=120)
    text = blocks.TextBlock(required=False, max_length=400)
    link = LinkBlock()

    class Meta:
        icon = "pick"
        label = _("Call to action")
        template = "cms/blocks/cta.html"


class FaqItemBlock(blocks.StructBlock):  # type: ignore[misc]
    question = blocks.CharBlock(max_length=250)
    answer = blocks.RichTextBlock(features=RICH_TEXT_FEATURES)

    class Meta:
        icon = "help"
        label = _("Question")


class FaqBlock(blocks.StructBlock):  # type: ignore[misc]
    """Questions shown as disclosures (`<details>`). The page template emits one FAQPage for all
    the FAQ blocks of the page (`cms_tags.faq_jsonld`, spec 10)."""

    heading = blocks.CharBlock(required=False, max_length=120)
    items = blocks.ListBlock(FaqItemBlock(), min_num=1, label=_("Questions"))

    class Meta:
        icon = "help"
        label = _("FAQ")
        template = "cms/blocks/faq.html"


# --- Cards (part 2/2)
#
# The targets of all the cards of a block are resolved in bulk (`_card_targets`): a constant
# number of queries however many cards a page shows (#117 review).


@dataclass(frozen=True)
class Card:
    label: str
    href: str
    text: str


@dataclass(frozen=True)
class _Target:
    """What a card shows by default: its target's name, URL and text in the reader's language."""

    name: str
    href: str
    text: str = ""


class _CardValue(blocks.StructValue):  # type: ignore[misc]
    target_kind: ClassVar[str]

    def target(self) -> Any:
        return self.get(self.target_kind)

    def card(self, targets: Mapping[tuple[str, int], _Target]) -> Card | None:
        """The card, or None when its target is not public (not in `targets`)."""
        chosen = self.target()
        target = None if chosen is None else targets.get((self.target_kind, chosen.pk))
        if target is None:
            return None
        return Card(self.get("label") or target.name, target.href, self.get("text") or target.text)


class PageCardValue(_CardValue):
    target_kind = "page"


class TermCardValue(_CardValue):
    target_kind = "term"


class VendorCardValue(_CardValue):
    target_kind = "vendor"


def _page_targets(pages: list[Page], language: str, request: Any) -> dict[int, _Target]:
    """Each page in `language` when that translation is live, else itself when it is live (the
    `live_page` rule): one query for all the translations."""
    keys = {page.translation_key for page in pages}
    translated = {
        page.translation_key: page
        for page in Page.objects.filter(
            translation_key__in=keys, locale__language_code=language, live=True
        )
    }
    targets: dict[int, _Target] = {}
    for page in pages:
        shown: Page | None = translated.get(page.translation_key) or (page if page.live else None)
        url = shown.get_url(request) if shown is not None else None
        if shown is not None and url:
            targets[page.pk] = _Target(shown.title, url)
    return targets


def _published_by_owner(rows: Iterable[Any], owner: str) -> dict[int, dict[str, Any]]:
    """Published translation rows grouped by their owner's id, then by language."""
    grouped: dict[int, dict[str, Any]] = defaultdict(dict)
    for row in rows:
        grouped[getattr(row, owner)][row.language] = row
    return grouped


def _term_targets(terms: list[Term], language: str) -> dict[int, _Target]:
    """Each term's name and listing URL in `language`, or in the primary language when the term,
    its taxonomy or a parent term is not published in `language` (spec 09 fallback). Three
    queries: the parent terms, the term texts and the taxonomy texts."""
    primary = translations.source_language()
    published = {"status": translations.PUBLISHED, "language__in": {language, primary}}

    def ancestor_paths(term: Term) -> list[str]:
        return [term.path[:end] for end in range(term.steplen, len(term.path), term.steplen)]

    paths = {path for term in terms for path in ancestor_paths(term)}
    nodes = {node.path: node for node in Term.objects.filter(path__in=paths)}
    nodes.update((term.path, term) for term in terms)
    term_texts = _published_by_owner(
        TermTranslation.objects.filter(term_id__in={n.pk for n in nodes.values()}, **published),
        "term_id",
    )
    taxonomy_texts = _published_by_owner(
        TaxonomyTranslation.objects.filter(
            taxonomy_id__in={term.taxonomy_id for term in terms}, **published
        ),
        "taxonomy_id",
    )
    targets: dict[int, _Target] = {}
    for term in terms:
        found = [nodes.get(path) for path in ancestor_paths(term)]
        chain = [node for node in found if node is not None]
        if len(chain) != len(found):
            continue
        texts = [
            taxonomy_texts.get(term.taxonomy_id, {}),
            *(term_texts.get(node.pk, {}) for node in (*chain, term)),
        ]
        chosen = link_language(language, primary, texts)
        if chosen is None:
            continue
        taxonomy, *slugs = (by_language[chosen] for by_language in texts)
        href = term_path(taxonomy.slug, [text.slug for text in slugs], chosen, primary)
        targets[term.pk] = _Target(slugs[-1].name, href)
    return targets


def _vendor_targets(vendors: list[Vendor], language: str) -> dict[int, _Target]:
    """Each active vendor's storefront URL and tagline: one query for all the taglines."""
    public = [vendor for vendor in vendors if has_public_storefront(vendor)]
    primary = translations.source_language()
    texts = _published_by_owner(
        VendorTranslation.objects.filter(
            vendor_id__in={vendor.pk for vendor in public},
            status=translations.PUBLISHED,
            language__in={language, primary},
        ),
        "vendor_id",
    )
    targets: dict[int, _Target] = {}
    for vendor in public:
        shown = translations.resolve(texts.get(vendor.pk, {}).values(), language).translation
        href = storefront_path(vendor.slug, language, primary)
        targets[vendor.pk] = _Target(
            vendor.display_name, href, shown.tagline if shown is not None else ""
        )
    return targets


def _card_targets(
    values: Iterable[_CardValue], language: str, request: Any
) -> dict[tuple[str, int], _Target]:
    """The public targets of `values`, keyed by (kind, id); a target that is not public is
    missing."""
    chosen: dict[str, list[Any]] = defaultdict(list)
    for value in values:
        if value.target() is not None:
            chosen[value.target_kind].append(value.target())
    resolved = {
        "page": _page_targets(chosen["page"], language, request) if chosen["page"] else {},
        "term": _term_targets(chosen["term"], language) if chosen["term"] else {},
        "vendor": _vendor_targets(chosen["vendor"], language) if chosen["vendor"] else {},
    }
    return {
        (kind, pk): target for kind, targets in resolved.items() for pk, target in targets.items()
    }


class TermChooserBlock(term_chooser.get_block_class("_TermChooserBlock", __name__)):  # type: ignore[misc]
    """A catalog taxonomy term, chosen in the term chooser modal."""


class VendorChooserBlock(vendor_chooser.get_block_class("_VendorChooserBlock", __name__)):  # type: ignore[misc]
    """A vendor with a public storefront, chosen in the vendor chooser modal."""

    def clean(self, value: Vendor | None) -> Vendor | None:
        value = super().clean(value)
        if value is not None and not has_public_storefront(value):
            raise ValidationError(_("Choose an active vendor."))
        return value


def _card_label() -> blocks.CharBlock:
    return blocks.CharBlock(
        required=False, max_length=100, help_text=_("Leave empty to use its name.")
    )


def _card_text() -> blocks.TextBlock:
    return blocks.TextBlock(required=False, max_length=250)


class PageCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a page of the site, in the reader's language."""

    page = blocks.PageChooserBlock()
    label = _card_label()
    text = _card_text()

    class Meta:
        icon = "doc-empty"
        label = _("Page")
        value_class = PageCardValue


class TermCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a catalog term's listing (spec 03), with its name in the reader's language."""

    term = TermChooserBlock()
    label = _card_label()
    text = _card_text()

    class Meta:
        icon = "tag"
        label = _("Term")
        value_class = TermCardValue


class VendorCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to an active vendor's storefront, with its tagline."""

    vendor = VendorChooserBlock()
    label = _card_label()
    text = _card_text()

    class Meta:
        icon = "group"
        label = _("Vendor")
        value_class = VendorCardValue


class FeaturedLinksBlock(blocks.StructBlock):  # type: ignore[misc]
    """A heading and cards to pages, terms and vendors; left out when no card is public."""

    heading = blocks.CharBlock(required=False, max_length=120)
    cards = blocks.StreamBlock(
        [("page", PageCardBlock()), ("term", TermCardBlock()), ("vendor", VendorCardBlock())],
        min_num=1,
        label=_("Cards"),
    )

    class Meta:
        icon = "list-ul"
        label = _("Featured links")
        template = "cms/blocks/featured.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        values = [child.value for child in value["cards"]]
        request = (parent_context or {}).get("request")
        targets = _card_targets(values, _page_language(parent_context), request)
        cards = (card_value.card(targets) for card_value in values)
        context["cards"] = [card for card in cards if card is not None]
        return context


# --- Product grid (CMS.10)


class CategoryChooserBlock(category_chooser.get_block_class("_CategoryChooserBlock", __name__)):  # type: ignore[misc]
    """A catalog category, chosen in the category chooser modal."""


class BrandChooserBlock(brand_chooser.get_block_class("_BrandChooserBlock", __name__)):  # type: ignore[misc]
    """A catalog brand, chosen in the brand chooser modal."""


class ProductGridBlock(blocks.StructBlock):  # type: ignore[misc]
    """The newest products of one category, term, brand or vendor (the CAT.8 listing query)."""

    heading = blocks.CharBlock(required=False, max_length=120)
    source = blocks.StreamBlock(
        [
            ("category", CategoryChooserBlock(label=_("Category"))),
            ("term", TermChooserBlock(label=_("Term"))),
            ("brand", BrandChooserBlock(label=_("Brand"))),
            ("vendor", VendorChooserBlock(label=_("Vendor"))),
        ],
        min_num=1,
        max_num=1,
        label=_("Products of"),
    )
    limit = blocks.IntegerBlock(
        min_value=1,
        max_value=PRODUCT_GRID_MAX,
        default=PRODUCT_GRID_DEFAULT,
        label=_("Number of products"),
    )
    see_all = blocks.BooleanBlock(required=False, default=True, label=_("Link to the full listing"))

    class Meta:
        icon = "grip"
        label = _("Product grid")
        template = "cms/blocks/product_grid.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        context["cards"], context["see_all_url"] = [], None
        chosen = next(iter(value["source"]), None)
        if chosen is None or chosen.value is None:
            return context
        heading = value.get("heading") or ""
        see_all = bool(value.get("see_all"))
        grid = product_grid(
            ListingScope(**{chosen.block_type: chosen.value}),
            _page_language(parent_context),
            limit=grid_limit(value.get("limit")),
            # The listing's name and page: for "See all", and as the heading when it is empty.
            with_anchor=see_all or not heading,
        )
        context["cards"] = grid.cards
        context["heading"] = heading or grid.name or ""
        context["listing_name"] = grid.name
        context["see_all_url"] = grid.see_all_url if see_all else None
        context["currency"] = CURRENCY
        return context


# --- Launch countdown (CMS.9)


def operator_time_zone() -> ZoneInfo:
    """The platform operator's time zone: editors enter launch times in it."""
    return ZoneInfo(settings.BUSINESS_TIME_ZONE)


class OperatorDateTimeField(forms.DateTimeField):
    """A date and time typed in the operator's time zone, cleaned to UTC, and shown in that
    zone again when the editor reopens the page (the admin itself runs in UTC)."""

    def prepare_value(self, value: Any) -> Any:
        if isinstance(value, dt.datetime) and timezone.is_aware(value):
            return timezone.localtime(value, operator_time_zone()).replace(tzinfo=None)
        return value

    def to_python(self, value: Any) -> dt.datetime | None:
        with timezone.override(operator_time_zone()):
            parsed: dt.datetime | None = super().to_python(value)
        return parsed.astimezone(dt.UTC) if parsed is not None else None


class OperatorDateTimeBlock(blocks.DateTimeBlock):  # type: ignore[misc]
    @cached_property
    def field(self) -> forms.DateTimeField:
        from wagtail.admin.widgets import AdminDateTimeInput

        return OperatorDateTimeField(
            widget=AdminDateTimeInput(format=self.format), **self.field_options
        )


class ProductChooserBlock(
    product_chooser.get_block_class("_ProductChooserBlock", __name__)  # type: ignore[misc]
):
    """A product a visitor may see, chosen in the product chooser modal."""


def _default_link(value: blocks.StructValue, language: str) -> str:
    """The public URL of the chosen product or category in `language`; "" when there is none
    or it is not public."""
    product: Product | None = value.get("product")
    category: Category | None = value.get("category")
    if product is not None:
        shown = listed_products().filter(pk=product.pk).first()
        url = product_cards([shown], language)[0].url if shown is not None else None
        return url or ""
    if category is not None:
        return category_url(category, language) or ""
    return ""


class LaunchCountdownBlock(blocks.StructBlock):  # type: ignore[misc]
    """A launch: the date, the time left and a CTA before it, a text and a link from it on."""

    target = OperatorDateTimeBlock(
        label=_("Launch date and time"),
        help_text=_("In the operator's time zone."),
    )
    heading = blocks.CharBlock(max_length=120)
    body = blocks.TextBlock(required=False, max_length=400)
    product = ProductChooserBlock(required=False)
    category = CategoryChooserBlock(required=False)
    cta_label = blocks.CharBlock(required=False, max_length=60, label=_("Button text"))
    cta_link = blocks.URLBlock(
        required=False,
        label=_("Button link"),
        help_text=_("Leave empty to link the chosen product or category."),
    )
    expired_text = blocks.CharBlock(
        required=False,
        max_length=120,
        label=_("Text after the launch"),
        help_text=_('Leave empty for "Available now".'),
    )
    expired_link = blocks.URLBlock(
        required=False,
        label=_("Link after the launch"),
        help_text=_("Leave empty to link the chosen product or category."),
    )

    class Meta:
        icon = "time"
        label = _("Launch countdown")
        template = "cms/blocks/countdown.html"

    def clean(self, value: blocks.StructValue) -> blocks.StructValue:
        value = super().clean(value)
        chosen = value.get("product") is not None or value.get("category") is not None
        if value.get("product") is not None and value.get("category") is not None:
            raise StructBlockValidationError(
                non_block_errors=[ValidationError(_("Choose a product or a category, not both."))]
            )
        if value.get("cta_label") and not value.get("cta_link") and not chosen:
            raise StructBlockValidationError(
                block_errors={
                    "cta_link": ValidationError(_("Add a link, or choose a product or a category."))
                }
            )
        return value

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        state = countdown_state(value["target"], timezone.now())
        missing = not value.get("cta_link") or not value.get("expired_link")
        default = _default_link(value, _page_language(parent_context)) if missing else ""
        context.update(
            state=state,
            cta_url=value.get("cta_link") or default,
            expired_url=value.get("expired_link") or default,
            expired_text=value.get("expired_text") or _("Available now"),
            time_zone=operator_time_zone(),
        )
        return context


def countdown_targets(body: Iterable[Any]) -> list[dt.datetime]:
    """The targets of the launch countdowns of a StreamField value."""
    return [child.value["target"] for child in body if child.block_type == "countdown"]

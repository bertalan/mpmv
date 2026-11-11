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

The blog's "featured links" (CMS.3, `ArticleLinksBlock`) also take product cards: a published
product of an active vendor, linked to its page in the reader's language (or in the language of
the text it shows). Other bodies keep the page, term and vendor cards.

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

from apps.catalog.models import (
    Category,
    Product,
    ProductTranslation,
    TaxonomyTranslation,
    Term,
    TermTranslation,
)
from apps.catalog.services import (
    ListingScope,
    ProductCard,
    ProductGrid,
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


# The required sections of the legal texts that DSA and P2B prescribe (CMP.5): an editor tags a
# section so that the compliance readiness check finds it, whatever the heading says or the
# language. The wording is the editor's; code never writes legal text.
LEGAL_SECTIONS: tuple[tuple[str, Any], ...] = (
    ("restrictions", _("Restrictions: what the platform may restrict, and why")),
    ("moderation_policy", _("Content moderation policy and procedures")),
    ("complaint_handling", _("Internal complaint handling (DSA article 20)")),
    ("redress", _("Out-of-court dispute settlement (DSA article 21)")),
    ("ranking_parameters", _("Ranking: main parameters and their relative importance")),
    ("differentiated_treatment", _("Differentiated treatment of vendors")),
    ("data_access", _("Access to data")),
    ("termination", _("Suspension and termination")),
)


class LegalSectionBlock(blocks.StructBlock):  # type: ignore[misc]
    """A section of a legal text that a regulation requires (DSA article 14, P2B articles 5, 7
    and 9). The `section` tag is what `apps.compliance` checks for; the heading and the text are
    written (and translated) by the editor."""

    section = blocks.ChoiceBlock(choices=list(LEGAL_SECTIONS), label=_("Required section"))
    heading = blocks.CharBlock(max_length=160)
    text = blocks.RichTextBlock(features=RICH_TEXT_FEATURES)

    class Meta:
        icon = "doc-full"
        label = _("Required section")
        template = "cms/blocks/legal_section.html"


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


class ProductCardValue(_CardValue):
    target_kind = "product"


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


def _product_targets(products: list[Any], language: str) -> dict[int, _Target]:
    """Each listed product's title and page URL (spec 09 fallback for both): two queries."""
    shown = list(listed_products().filter(pk__in={product.pk for product in products}))
    targets: dict[int, _Target] = {}
    for product, card in zip(shown, product_cards(shown, language), strict=True):
        if card.url:
            targets[product.pk] = _Target(card.title, card.url)
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
        "product": _product_targets(chosen["product"], language) if chosen["product"] else {},
    }
    return {
        (kind, pk): target for kind, targets in resolved.items() for pk, target in targets.items()
    }


def public_cards(values: Iterable[_CardValue], language: str, request: Any) -> list[Card]:
    """The cards of `values` whose target is public, in order (a landing page's related
    articles, CMS.4)."""
    values = list(values)
    targets = _card_targets(values, language, request)
    cards = (value.card(targets) for value in values)
    return [card for card in cards if card is not None]


class TermChooserBlock(term_chooser.get_block_class("_TermChooserBlock", __name__)):  # type: ignore[misc]
    """A catalog taxonomy term, chosen in the term chooser modal."""


class VendorChooserBlock(vendor_chooser.get_block_class("_VendorChooserBlock", __name__)):  # type: ignore[misc]
    """A vendor with a public storefront, chosen in the vendor chooser modal."""

    def clean(self, value: Vendor | None) -> Vendor | None:
        value = super().clean(value)
        if value is not None and not has_public_storefront(value):
            raise ValidationError(_("Choose an active vendor."))
        return value


class ProductChooserBlock(product_chooser.get_block_class("_ProductChooserBlock", __name__)):  # type: ignore[misc]
    """A published product of an active vendor, chosen in the product chooser modal."""


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


class ProductCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a product page (CMS.3), with its title in the reader's language."""

    product = ProductChooserBlock()
    label = _card_label()
    text = _card_text()

    class Meta:
        icon = "pick"
        label = _("Product")
        value_class = ProductCardValue


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


class ArticleLinksBlock(FeaturedLinksBlock):
    """The blog's featured links (CMS.3): cards to pages, terms, vendors and products."""

    cards = blocks.StreamBlock(
        [
            ("page", PageCardBlock()),
            ("term", TermCardBlock()),
            ("vendor", VendorCardBlock()),
            ("product", ProductCardBlock()),
        ],
        min_num=1,
        label=_("Cards"),
    )

    class Meta:
        icon = "list-ul"
        label = _("Featured links")
        template = "cms/blocks/featured.html"


# --- Product grid (CMS.10)


class CategoryChooserBlock(category_chooser.get_block_class("_CategoryChooserBlock", __name__)):  # type: ignore[misc]
    """A catalog category, chosen in the category chooser modal."""


class BrandChooserBlock(brand_chooser.get_block_class("_BrandChooserBlock", __name__)):  # type: ignore[misc]
    """A catalog brand, chosen in the brand chooser modal."""


def _item_list(
    name: str, cards: Iterable[ProductCard], context: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """One schema.org `ItemList` of product references (spec 10 "Widgets feed the graph"): the
    `@id` of a product is its page; a list item never repeats its offers (the product's own page
    carries the `Product` node). A card without a page, and a list without items, give nothing."""
    request = context.get("request")
    items: list[dict[str, Any]] = []
    for card in cards:
        if not card.url:
            continue
        url = request.build_absolute_uri(card.url) if request is not None else card.url
        item: dict[str, Any] = {"@id": url, "name": card.title, "url": url}
        if card.image_url:
            item["image"] = card.image_url
        items.append({"@type": "ListItem", "position": len(items) + 1, "item": item})
    if not items:
        return []
    return [
        {
            "@type": "ItemList",
            "name": name,
            "numberOfItems": len(items),
            "itemListElement": items,
        }
    ]


def page_graph_nodes(body: Iterable[Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The `@graph` nodes of a page body's widgets, in order: every block that implements
    `graph_nodes(value, context)` (the product grid and the product cards, SEO.7)."""
    nodes: list[dict[str, Any]] = []
    for child in body:
        graph_nodes = getattr(child.block, "graph_nodes", None)
        if graph_nodes is not None:
            nodes.extend(graph_nodes(child.value, context))
    return nodes


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

    def _grid(self, value: Any, parent_context: Mapping[str, Any] | None) -> ProductGrid | None:
        chosen = next(iter(value["source"]), None)
        if chosen is None or chosen.value is None:
            return None
        return product_grid(
            ListingScope(**{chosen.block_type: chosen.value}),
            _page_language(dict(parent_context) if parent_context else None),
            limit=grid_limit(value.get("limit")),
            # The listing's name and page: for "See all", and as the heading when it is empty.
            with_anchor=bool(value.get("see_all")) or not value.get("heading"),
        )

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        context["cards"], context["see_all_url"] = [], None
        grid = self._grid(value, parent_context)
        if grid is None:
            return context
        context["cards"] = grid.cards
        context["heading"] = value.get("heading") or grid.name or ""
        context["listing_name"] = grid.name
        context["see_all_url"] = grid.see_all_url if value.get("see_all") else None
        context["currency"] = CURRENCY
        return context

    def graph_nodes(self, value: Any, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The grid's named `ItemList` (SEO.7): its heading, else its listing's name."""
        grid = self._grid(value, context)
        if grid is None:
            return []
        name = value.get("heading") or grid.name or str(_("Products"))
        return _item_list(name, grid.cards, context)


# --- Product cards (CMS.11)

PRODUCT_CARDS_LAYOUTS = ("grid", "row")


def _shown_in_language(products: Iterable[Product | None], language: str) -> list[Product]:
    """The chosen products that are listed (published, of an active vendor, not retired) and
    published in `language`, in the editor's order. No text from another language stands in:
    a product the page language lacks is left out (CMS.11)."""
    wanted = list(dict.fromkeys(product.pk for product in products if product is not None))
    if not wanted:
        return []
    texts = ProductTranslation.objects.filter(
        product_id__in=wanted, language=language, status=translations.PUBLISHED
    ).values("product_id")
    found = {
        product.pk: product
        for product in listed_products().filter(pk__in=wanted).filter(pk__in=texts)
    }
    return [found[pk] for pk in wanted if pk in found]


class ProductCardsBlock(blocks.StructBlock):  # type: ignore[misc]
    """Hand-picked products as cards, in a grid or one scrolling row (CMS.11): the same block in
    every body that offers products. A product that is retired, unpublished or not published in
    the page's language disappears without editing the page; a block with none left is left
    out. Editors search the product chooser; the cards show the price as the grid does."""

    heading = blocks.CharBlock(
        required=False,
        max_length=120,
        help_text=_(
            "A title shown above the products, for example 'Our favourites'. "
            "Up to 120 characters. If you leave it empty, no title is shown."
        ),
    )
    layout = blocks.ChoiceBlock(
        choices=[("grid", _("Grid")), ("row", _("Row"))],
        default="grid",
        label=_("Layout"),
        help_text=_(
            "Grid shows the products in rows and columns. Row puts them on one line "
            "that the visitor can scroll sideways."
        ),
    )
    products = blocks.ListBlock(
        ProductChooserBlock(
            help_text=_("Search by name, SKU, brand or vendor, then click the product.")
        ),
        min_num=1,
        label=_("Products"),
        help_text=_(
            "The products to show, in this order. Add at least one. A product that is no "
            "longer for sale, or has no text in the page language, is left out by itself."
        ),
    )

    class Meta:
        icon = "pick"
        label = _("Product cards")
        template = "cms/blocks/product_cards.html"

    def _cards(self, value: Any, parent_context: Mapping[str, Any] | None) -> list[ProductCard]:
        language = _page_language(dict(parent_context) if parent_context else None)
        return product_cards(_shown_in_language(value["products"], language), language)

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        context["cards"] = self._cards(value, parent_context)
        context["heading"] = value.get("heading") or ""
        context["row"] = value.get("layout") == "row"
        context["currency"] = CURRENCY
        return context

    def graph_nodes(self, value: Any, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The cards' named `ItemList` (SEO.7): the heading, else a generic name."""
        return _item_list(
            value.get("heading") or str(_("Products")), self._cards(value, context), context
        )


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

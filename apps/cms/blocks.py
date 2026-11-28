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
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone, translation
from django.utils.formats import number_format
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.blocks import StructBlockValidationError
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Page
from wagtail.snippets.blocks import SnippetChooserBlock

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
from apps.core.images import rendition_or_original
from apps.core.money import CURRENCY
from apps.core.services import business_tz
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.seller_name import public_name
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
    file_size_parts,
    file_type_label,
    gallery_for_language,
    grid_limit,
    link_language,
    parse_video_url,
    storefront_path,
    term_path,
)

# Editors may use headings, emphasis, lists and links; no raw HTML, images or embeds yet.
RICH_TEXT_FEATURES = ["h2", "h3", "bold", "italic", "ol", "ul", "link"]


def paragraph_block() -> blocks.RichTextBlock:
    """The rich-text paragraph of every page body (one definition, one help text)."""
    return blocks.RichTextBlock(
        features=RICH_TEXT_FEATURES,
        label=_("Rich text"),
        help_text=_(
            "Write your text here. Use the toolbar for headings, bold, lists and links. "
            "A heading makes the page easier to scan."
        ),
    )


def language_choices() -> list[tuple[str, Any]]:
    """Neutral first, then every configured language; read at runtime, so no migration."""
    return [(NEUTRAL, _("All languages (neutral)")), *settings.LANGUAGES]


class FigureBlock(blocks.StructBlock):  # type: ignore[misc]  # Wagtail is untyped
    """One image with its alt text and an optional caption."""

    image = ImageChooserBlock(
        help_text=_("Pick the picture from the library or upload a new one. Required.")
    )
    alt_text = blocks.CharBlock(
        max_length=250,
        help_text=_(
            "Describe the picture in a few words, for people who cannot see it. "
            "Example: 'Red ceramic mug on a wooden table'. Required: the picture cannot be saved "
            "without it."
        ),
    )
    caption = blocks.CharBlock(
        max_length=250,
        required=False,
        help_text=_(
            "A short line shown under the picture, up to 250 characters. "
            "If you leave it empty, no caption is shown."
        ),
    )

    class Meta:
        icon = "image"
        label = _("Image")
        template = "cms/blocks/figure.html"


class GalleryImageBlock(FigureBlock):
    """A gallery image, neutral or dedicated to one language (D-18)."""

    language = blocks.ChoiceBlock(
        choices=language_choices,
        required=False,
        help_text=_(
            "Pick a language to show this picture only to visitors reading in it. "
            "Leave it on 'All languages' to show it to everyone."
        ),
    )


def _gallery_slide(item: Mapping[str, Any]) -> dict[str, Any]:
    """A gallery image as the context of the theme's gallery carousel."""
    image = item["image"]
    # A page never builds a rendition (CAT.4): the `core.render_image` job did; until it ran, the
    # original file is shown.
    url, width, height = rendition_or_original(image, "width-800")
    large_url, _, _ = rendition_or_original(image, "width-1600")
    return {
        "url": url,
        "large_url": large_url,
        "alt": item.get("alt_text") or "",
        "caption": item.get("caption") or "",
        "width": width,
        "height": height,
    }


class GalleryBlock(blocks.StructBlock):  # type: ignore[misc]
    """Images shown by the D-18 rule: a language's dedicated images, otherwise the neutral."""

    images = blocks.ListBlock(GalleryImageBlock())

    class Meta:
        icon = "image"
        label = _("Gallery")
        template = "cms/blocks/gallery.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        images = gallery_for_language(value["images"], _page_language(parent_context))
        context["images"] = images
        context["slides"] = [_gallery_slide(image) for image in images]
        context["carousel_id"] = _unique_id(parent_context, "gallery", value)
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

    page = blocks.PageChooserBlock(
        required=False,
        help_text=_(
            "Choose a page of this site to link to. Fill in this or the web address below, not "
            "both."
        ),
    )
    url = blocks.URLBlock(
        required=False,
        label=_("URL"),
        help_text=_(
            "The full address of another website, for example 'https://example.com'. "
            "Fill in this or the page above, not both."
        ),
    )
    label = blocks.CharBlock(
        max_length=100,
        help_text=_(
            "The words visitors click, for example 'Read more'. Required: without it the link "
            "cannot be saved."
        ),
    )

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


class HeroSlideBlock(blocks.StructBlock):  # type: ignore[misc]
    """One more slide of a slider hero (THM.14): a picture, a title, a sentence and up to two
    buttons."""

    image = ImageChooserBlock(
        required=False,
        help_text=_("A picture for the slide. If you leave it empty, the slide has no picture."),
    )
    alt_text = blocks.CharBlock(
        required=False,
        max_length=250,
        help_text=_(
            "Describe the picture in a few words, for people who cannot see it. "
            "Required when you choose a picture."
        ),
    )
    heading = blocks.CharBlock(
        max_length=120,
        help_text=_("The slide's title, up to 120 characters. Required."),
    )
    text = blocks.TextBlock(
        required=False,
        max_length=400,
        help_text=_("One or two sentences under the title. If you leave it empty, only the title."),
    )
    links = blocks.ListBlock(
        LinkBlock(),
        min_num=0,
        max_num=2,
        default=[],
        help_text=_("Up to two buttons on the slide. If you add none, the slide has no buttons."),
    )

    class Meta:
        icon = "image"
        label = _("Slide")

    def clean(self, value: blocks.StructValue) -> blocks.StructValue:
        value = super().clean(value)
        if value.get("image") is not None and not (value.get("alt_text") or "").strip():
            raise StructBlockValidationError(
                block_errors={"alt_text": ValidationError(_("Describe the image."))}
            )
        return value


def _slide(value: Mapping[str, Any]) -> dict[str, Any]:
    """A hero or a slide as the context of the theme's hero component."""
    links = list(value.get("links") or [])
    first = links[0] if links else None
    second = links[1] if len(links) > 1 else None
    return {
        "title": value.get("heading") or "",
        "text": value.get("text") or "",
        "image": value.get("image"),
        "image_alt": value.get("alt_text") or "",
        "url": first.href() if first else "",
        "action": first.get("label") if first else "",
        "secondary_url": second.href() if second else "",
        "secondary_action": second.get("label") if second else "",
    }


def _unique_id(context: Mapping[str, Any] | None, prefix: str, value: Any) -> str:
    """`prefix-N`, N counting up per request, so two carousels on a page never share an id.
    Without a request (a block rendered alone) the value's identity stands in."""
    request = (context or {}).get("request")
    if request is None:
        return f"{prefix}-{id(value) % 100003}"
    counters: dict[str, int] = request.__dict__.setdefault("_carousel_ids", {})
    counters[prefix] = counters.get(prefix, 0) + 1
    return f"{prefix}-{counters[prefix]}"


class HeroValue(blocks.StructValue):  # type: ignore[misc]
    @property
    def is_slider(self) -> bool:
        """A slider needs a slide besides the banner itself; without one it is a single image."""
        return bool(self.get("layout") == "slider" and len(self.get("slides") or []) > 0)


class HeroBlock(blocks.StructBlock):  # type: ignore[misc]
    """The opening banner of a page: a heading, a short text, an image and up to two links; as
    a single image, a split (text beside the picture) or a slider of slides (THM.14)."""

    heading = blocks.CharBlock(
        max_length=120,
        help_text=_(
            "The big title at the top of the page, up to 120 characters. "
            "Required: the banner cannot be saved without it."
        ),
    )
    text = blocks.TextBlock(
        required=False,
        max_length=400,
        help_text=_(
            "One or two sentences under the title, up to 400 characters. "
            "If you leave it empty, only the title is shown."
        ),
    )
    image = ImageChooserBlock(
        required=False,
        help_text=_("A picture for the banner. If you leave it empty, the banner has no picture."),
    )
    alt_text = blocks.CharBlock(
        required=False,
        max_length=250,
        help_text=_(
            "Describe the picture in a few words, for people who cannot see it. "
            "Example: 'Potter shaping a bowl'. Required when you choose a picture."
        ),
    )
    links = blocks.ListBlock(
        LinkBlock(),
        min_num=0,
        max_num=2,
        default=[],
        help_text=_(
            "Up to two buttons under the text. If you add none, the banner has no buttons."
        ),
    )
    layout = blocks.ChoiceBlock(
        choices=[
            ("single", _("Single image")),
            ("slider", _("Slider")),
            ("split", _("Text beside the image")),
        ],
        default="single",
        label=_("Layout"),
        help_text=_(
            "Single image: the picture fills the banner. Text beside the image: the text and "
            "the picture sit side by side on wide screens. Slider: the banner above is the "
            "first slide and the slides below follow."
        ),
    )
    slides = blocks.ListBlock(
        HeroSlideBlock(),
        min_num=0,
        max_num=6,
        default=[],
        label=_("More slides"),
        help_text=_(
            "Used by the slider layout: up to six slides after the banner. "
            "Without slides the banner shows as a single image."
        ),
    )
    autoplay = blocks.BooleanBlock(
        required=False,
        default=False,
        label=_("Change slides by itself"),
        help_text=_(
            "Slider only. Off by default. When on, the slides change on their own, stop when "
            "the visitor hovers or tabs into the slider, and never move for people who asked "
            "their device for less motion."
        ),
    )
    interval = blocks.IntegerBlock(
        required=False,
        default=6000,
        min_value=3000,
        max_value=20000,
        label=_("Time between slides (milliseconds)"),
        help_text=_(
            "Slider with automatic change only: how long a slide stays, in milliseconds, "
            "between 3000 and 20000. Example: 6000 is six seconds."
        ),
    )

    class Meta:
        icon = "pick"
        label = _("Hero")
        template = "cms/blocks/hero.html"
        value_class = HeroValue
        # No caption area: a credited image goes to the page's "Image credits" (CMS.8).
        credit_in_disclosure = True

    def clean(self, value: blocks.StructValue) -> blocks.StructValue:
        value = super().clean(value)
        if value.get("image") is not None and not (value.get("alt_text") or "").strip():
            raise StructBlockValidationError(
                block_errors={"alt_text": ValidationError(_("Describe the image."))}
            )
        return value

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        slider = value.is_slider
        context["slider"] = slider
        context["slides"] = (
            [_slide(value), *(_slide(slide) for slide in value["slides"])] if slider else []
        )
        context["extra_slides"] = [_slide(slide) for slide in value["slides"]] if slider else []
        context["autoplay"] = bool(value.get("autoplay")) and slider
        context["interval"] = value.get("interval") or 6000
        context["carousel_id"] = _unique_id(parent_context, "hero-slider", value) if slider else ""
        return context


class CallToActionBlock(blocks.StructBlock):  # type: ignore[misc]
    """A heading, a short text and one link that invites the reader to act."""

    heading = blocks.CharBlock(
        max_length=120,
        help_text=_(
            "The invitation, for example 'Visit our workshop'. Up to 120 characters. "
            "Required: the block cannot be saved without it."
        ),
    )
    text = blocks.TextBlock(
        required=False,
        max_length=400,
        help_text=_(
            "A sentence or two that says why to click, up to 400 characters. "
            "If you leave it empty, only the title and the button are shown."
        ),
    )
    link = LinkBlock(help_text=_("The button visitors click. Required."))

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

    section = blocks.ChoiceBlock(
        choices=list(LEGAL_SECTIONS),
        label=_("Required section"),
        help_text=_(
            "Which section the law asks for this text to be. The platform checks that every "
            "required section exists, whatever the title says."
        ),
    )
    heading = blocks.CharBlock(
        max_length=160,
        help_text=_(
            "The title of the section as visitors read it, up to 160 characters. Required."
        ),
    )
    text = blocks.RichTextBlock(
        features=RICH_TEXT_FEATURES,
        help_text=_(
            "The text of the section. Write it as your legal adviser approved it. Required."
        ),
    )

    class Meta:
        icon = "doc-full"
        label = _("Required section")
        template = "cms/blocks/legal_section.html"


class FaqItemBlock(blocks.StructBlock):  # type: ignore[misc]
    question = blocks.CharBlock(
        max_length=250,
        help_text=_(
            "The question as a customer would ask it, for example 'How long does delivery take?'. "
            "Required: the question cannot be saved without it."
        ),
    )
    answer = blocks.RichTextBlock(
        features=RICH_TEXT_FEATURES,
        help_text=_(
            "A clear, short answer. It opens when the visitor clicks the question. Required."
        ),
    )

    class Meta:
        icon = "help"
        label = _("Question")


class FaqBlock(blocks.StructBlock):  # type: ignore[misc]
    """Questions shown as disclosures (`<details>`). The page template emits one FAQPage for all
    the FAQ blocks of the page (`cms_tags.faq_jsonld`, spec 10)."""

    heading = blocks.CharBlock(
        required=False,
        max_length=120,
        help_text=_(
            "A title above the questions, for example 'Questions and answers'. "
            "If you leave it empty, the questions have no title."
        ),
    )
    items = blocks.ListBlock(
        FaqItemBlock(),
        min_num=1,
        label=_("Questions"),
        help_text=_(
            "Add at least one question. They show as a list that opens one answer at a time."
        ),
    )

    class Meta:
        icon = "help"
        label = _("FAQ")
        template = "cms/blocks/faq.html"


class AccordionSectionBlock(blocks.StructBlock):  # type: ignore[misc]
    title = blocks.CharBlock(
        max_length=250,
        help_text=_("The title of the section, always visible. Required."),
    )
    body = blocks.RichTextBlock(
        features=RICH_TEXT_FEATURES,
        help_text=_("The text that shows when the visitor opens the section. Required."),
    )

    class Meta:
        icon = "list-ul"
        label = _("Section")


class AccordionBlock(blocks.StructBlock):  # type: ignore[misc]
    """Titled sections that open and close, built on native `<details>` and `<summary>` (D-36,
    CMS.16): no script, keyboard accessible, closed by default. It is not a FAQ: the page emits
    no FAQPage data for it (only the `faq` block does)."""

    sections = blocks.ListBlock(
        AccordionSectionBlock(),
        min_num=1,
        label=_("Sections"),
        help_text=_("Add at least one section. Each has a title and a text that opens on click."),
    )
    open_first = blocks.BooleanBlock(
        required=False,
        label=_("Open the first section"),
        help_text=_("Tick this to show the first section already open. Otherwise all are closed."),
    )

    class Meta:
        icon = "list-ul"
        label = _("Accordion")
        template = "cms/blocks/accordion.html"


# --- Cards (part 2/2)
#
# The targets of all the cards of a block are resolved in bulk (`_card_targets`): a constant
# number of queries however many cards a page shows (#117 review).


@dataclass(frozen=True)
class Card:
    label: str
    href: str
    text: str
    image: Any = None
    image_alt: str = ""


@dataclass(frozen=True)
class _Target:
    """What a card shows by default: its target's name, URL and text in the reader's language."""

    name: str
    href: str
    text: str = ""
    image: Any = None
    image_alt: str = ""


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
        label = self.get("label") or target.name
        text = self.get("text") or target.text
        own = self.get("image")
        if own:  # the editor's picture is decoration next to the title: no alt text
            return Card(label, target.href, text, own, "")
        return Card(label, target.href, text, target.image, target.image_alt)


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
    shown_pages = {
        page.pk: translated.get(page.translation_key) or (page if page.live else None)
        for page in pages
    }
    posts = _post_images({s.pk for s in shown_pages.values() if s is not None})
    for page in pages:
        shown = shown_pages[page.pk]
        url = shown.get_url(request) if shown is not None else None
        if shown is not None and url:
            post = posts.get(shown.pk)
            image = post.image if post is not None else None
            alt = post.image_alt_text if post is not None else ""
            targets[page.pk] = _Target(shown.title, url, "", image, alt)
    return targets


def _post_images(page_pks: set[int]) -> dict[int, Any]:
    """The blog posts among `page_pks` that have a main image: one query."""
    from .models import BlogPostPage  # models import this module

    posts = BlogPostPage.objects.filter(pk__in=page_pks, image__isnull=False)
    return {post.pk: post for post in posts.select_related("image")}


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
        name = public_name(vendor)
        if name is None:  # a person's legal name is never a card title (SEO.15)
            continue
        targets[vendor.pk] = _Target(name, href, shown.tagline if shown is not None else "")
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
    missing. The result is kept on the request: the page graph and the block's own render ask
    for the same cards, and the lookups run once."""
    chosen: dict[str, list[Any]] = defaultdict(list)
    for value in values:
        if value.target() is not None:
            chosen[value.target_kind].append(value.target())
    key = (language, tuple((kind, tuple(t.pk for t in found)) for kind, found in chosen.items()))
    kept: dict[Any, dict[tuple[str, int], _Target]] | None = None
    if request is not None:
        kept = request.__dict__.setdefault("_cms_card_targets", {})
        if key in kept:
            return kept[key]
    resolved = {
        "page": _page_targets(chosen["page"], language, request) if chosen["page"] else {},
        "term": _term_targets(chosen["term"], language) if chosen["term"] else {},
        "vendor": _vendor_targets(chosen["vendor"], language) if chosen["vendor"] else {},
        "product": _product_targets(chosen["product"], language) if chosen["product"] else {},
    }
    found_targets = {
        (kind, pk): target for kind, targets in resolved.items() for pk, target in targets.items()
    }
    if kept is not None:
        kept[key] = found_targets
    return found_targets


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
        required=False,
        max_length=100,
        help_text=_(
            "The title on the card, up to 100 characters. "
            "If you leave it empty, the card uses the name of what it points to."
        ),
    )


def _card_image() -> ImageChooserBlock:
    return ImageChooserBlock(
        required=False,
        help_text=_(
            "A picture for the card: the home page shows it behind the title and the blog "
            "strip above it. If you leave it empty, a card to a blog post uses that post's "
            "main image, and any other card shows the theme's own artwork."
        ),
    )


def _card_text() -> blocks.TextBlock:
    return blocks.TextBlock(
        required=False,
        max_length=250,
        help_text=_(
            "A short line under the title, up to 250 characters. "
            "If you leave it empty, the card uses the short text of what it points to, if any."
        ),
    )


class PageCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a page of the site, in the reader's language."""

    page = blocks.PageChooserBlock(
        help_text=_(
            "Choose the page the card leads to. If the page is not live, the card is left out."
        )
    )
    label = _card_label()
    text = _card_text()
    image = _card_image()

    class Meta:
        icon = "doc-empty"
        label = _("Page")
        value_class = PageCardValue


class TermCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a catalog term's listing (spec 03), with its name in the reader's language."""

    term = TermChooserBlock(
        help_text=_(
            "Choose the category or tag the card leads to. A card to a hidden one is left out."
        )
    )
    label = _card_label()
    text = _card_text()
    image = _card_image()

    class Meta:
        icon = "tag"
        label = _("Term")
        value_class = TermCardValue


class VendorCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to an active vendor's storefront, with its tagline."""

    vendor = VendorChooserBlock(
        help_text=_(
            "Choose the shop the card leads to. A card to a shop that is not open is left out."
        )
    )
    label = _card_label()
    text = _card_text()
    image = _card_image()

    class Meta:
        icon = "group"
        label = _("Vendor")
        value_class = VendorCardValue


class ProductCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a product page (CMS.3), with its title in the reader's language."""

    product = ProductChooserBlock(
        help_text=_(
            "Choose the product the card leads to. A card to a product that is not on sale is "
            "left out."
        )
    )
    label = _card_label()
    text = _card_text()
    image = _card_image()

    class Meta:
        icon = "pick"
        label = _("Product")
        value_class = ProductCardValue


def _href_in_language(href: str, language: str) -> bool:
    """Whether a site path is one of `language`'s URLs: a card that falls back to another
    language's page (a term without a text in the page language) stays out of the graph."""
    default = settings.LANGUAGE_CODE
    if not href.startswith("/"):
        return True
    others = [f"/{code}/" for code, _name in settings.LANGUAGES if code != default]
    if language == default:
        return not any(href.startswith(prefix) for prefix in others)
    return href.startswith(f"/{language}/")


def _cards_item_list(
    name: str, cards: Iterable[Card], context: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """One schema.org `ItemList` of the public cards of a featured links block or a strip
    (SEO.7): each item is the card's target page (`@id` and `url`) with its label, in the page
    language only. No card, no list."""
    request = context.get("request")
    language = _page_language(dict(context))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        if not card.href or not _href_in_language(card.href, language):
            continue
        url = request.build_absolute_uri(card.href) if request is not None else card.href
        if url in seen:
            continue
        seen.add(url)
        item = {"@id": url, "name": card.label, "url": url}
        items.append({"@type": "ListItem", "position": len(items) + 1, "item": item})
    if not items:
        return []
    return [
        {"@type": "ItemList", "name": name, "numberOfItems": len(items), "itemListElement": items}
    ]


class FeaturedLinksBlock(blocks.StructBlock):  # type: ignore[misc]
    """A heading and cards to pages, terms and vendors; left out when no card is public."""

    heading = blocks.CharBlock(
        required=False,
        max_length=120,
        help_text=_(
            "A title above the cards, for example 'You may also like'. "
            "If you leave it empty, the cards have no title."
        ),
    )
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

    def graph_nodes(self, value: Any, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The block's named `ItemList` (SEO.7): its heading, else a generic name."""
        values = [child.value for child in value["cards"]]
        targets = _card_targets(values, _page_language(dict(context)), context.get("request"))
        cards = [card for card in (v.card(targets) for v in values) if card is not None]
        return _cards_item_list(value.get("heading") or str(_("Featured links")), cards, context)


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


class TestimonialBlock(blocks.StructBlock):  # type: ignore[misc]
    """A quote from a customer, shown in a strip (THM.14)."""

    quote = blocks.TextBlock(
        max_length=400,
        help_text=_("What the person said, up to 400 characters. Required."),
    )
    author = blocks.CharBlock(
        max_length=120,
        help_text=_("Who said it, for example 'Ada'. Required."),
    )
    role = blocks.CharBlock(
        required=False,
        max_length=120,
        help_text=_("A short line about the person, for example 'Rider'. Optional."),
    )

    class Meta:
        icon = "openquote"
        label = _("Testimonial")


class StripBlock(blocks.StructBlock):  # type: ignore[misc]
    """A row of cards to pages (blog posts), vendors, products and customer quotes (THM.14): a
    carousel in the `ride` theme, a plain list in `base`. Cards whose target is not public
    are left out; a strip with none left is left out."""

    heading = blocks.CharBlock(
        required=False,
        max_length=120,
        help_text=_(
            "A title above the strip, for example 'From the blog'. "
            "Up to 120 characters. If you leave it empty, the strip has no title."
        ),
    )
    cards = blocks.StreamBlock(
        [
            ("page", PageCardBlock()),
            ("vendor", VendorCardBlock()),
            ("product", ProductCardBlock()),
            ("testimonial", TestimonialBlock()),
        ],
        min_num=1,
        label=_("Cards"),
        help_text=_(
            "Add cards of the same kind: blog posts or pages, vendors, products or testimonials."
        ),
    )
    per_view = blocks.ChoiceBlock(
        choices=[("4", _("Four")), ("2", _("Two")), ("1", _("One"))],
        default="4",
        label=_("Cards per slide"),
        help_text=_(
            "How many cards a slide shows on a wide screen. Narrow screens show fewer. "
            "Only the carousel theme uses it."
        ),
    )
    autoplay = blocks.BooleanBlock(
        required=False,
        default=False,
        label=_("Change slides by itself"),
        help_text=_(
            "Off by default. When on, the slides change on their own, stop when the visitor "
            "hovers or tabs into the strip, and never move for people who asked their "
            "device for less motion."
        ),
    )
    interval = blocks.IntegerBlock(
        required=False,
        default=6000,
        min_value=3000,
        max_value=20000,
        label=_("Time between slides (milliseconds)"),
        help_text=_(
            "Automatic change only: how long a slide stays, in milliseconds, between 3000 "
            "and 20000. Example: 6000 is six seconds."
        ),
    )

    class Meta:
        icon = "list-ul"
        label = _("Strip")
        template = "cms/blocks/strip.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        children = list(value["cards"])
        values = [child.value for child in children if child.block_type != "testimonial"]
        request = (parent_context or {}).get("request")
        targets = _card_targets(values, _page_language(parent_context), request)
        items: list[dict[str, Any]] = []
        for child in children:
            if child.block_type == "testimonial":
                item = child.value
                items.append(
                    {
                        "title": item["author"],
                        "text": item["quote"],
                        "meta": item.get("role") or "",
                        "url": "",
                        "quote": True,
                    }
                )
                continue
            card = child.value.card(targets)
            if card is not None:
                items.append(
                    {
                        "title": card.label,
                        "text": card.text,
                        "meta": "",
                        "url": card.href,
                        "image": card.image,
                        "image_alt": card.image_alt,
                        "quote": False,
                    }
                )
        context["items"] = items
        context["per_view"] = int(value.get("per_view") or 4)
        context["autoplay"] = bool(value.get("autoplay"))
        context["interval"] = value.get("interval") or 6000
        context["carousel_id"] = _unique_id(parent_context, "strip", value)
        return context

    def graph_nodes(self, value: Any, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The strip's named `ItemList` (SEO.7): its cards that lead to a page, not the quotes."""
        values = [c.value for c in value["cards"] if c.block_type != "testimonial"]
        targets = _card_targets(values, _page_language(dict(context)), context.get("request"))
        cards = [card for card in (v.card(targets) for v in values) if card is not None]
        return _cards_item_list(value.get("heading") or str(_("Featured")), cards, context)


class SectionTitleBlock(blocks.StructBlock):  # type: ignore[misc]
    """A heading with a small line above it and an optional lead (THM.14): the `ride` theme styles
    it with the accent rule, `base` shows a plain heading."""

    eyebrow = blocks.CharBlock(
        required=False,
        max_length=80,
        label=_("Line above the title"),
        help_text=_(
            "A few words shown small above the title, for example 'Our range'. "
            "Up to 80 characters. If you leave it empty, nothing is shown above."
        ),
    )
    title = blocks.CharBlock(
        max_length=120,
        help_text=_("The heading of the section, up to 120 characters. Required."),
    )
    lead = blocks.CharBlock(
        required=False,
        max_length=250,
        label=_("Short text below"),
        help_text=_(
            "One or two sentences under the title, up to 250 characters. "
            "If you leave it empty, nothing is shown below."
        ),
    )

    class Meta:
        icon = "title"
        label = _("Section title")
        template = "cms/blocks/section_title.html"


class CounterBlock(blocks.StructBlock):  # type: ignore[misc]
    """A number with a label (THM.14): the `ride` theme counts up to it once, when it scrolls into
    view and the visitor accepts motion; the final value is always in the page."""

    label = blocks.CharBlock(
        max_length=80,
        help_text=_("What the number counts, for example 'Orders shipped'. Up to 80 characters."),
    )
    value = blocks.IntegerBlock(
        min_value=0,
        max_value=999_999_999,
        help_text=_("The number to show, a whole number. Example: 1250."),
    )
    suffix = blocks.CharBlock(
        required=False,
        max_length=8,
        help_text=_(
            "A short sign after the number, for example '+' or '%'. "
            "If you leave it empty, the number stands alone."
        ),
    )

    class Meta:
        icon = "order"
        label = _("Counter")
        template = "cms/blocks/counter.html"


class ProgressBlock(blocks.StructBlock):  # type: ignore[misc]
    """A labelled progress bar (THM.14); the value is also written as text."""

    label = blocks.CharBlock(
        max_length=80,
        help_text=_("What the bar measures, for example 'Order packed'. Up to 80 characters."),
    )
    value = blocks.IntegerBlock(
        min_value=0,
        help_text=_("How far along it is. It cannot be more than the maximum below."),
    )
    max = blocks.IntegerBlock(
        min_value=1,
        default=100,
        label=_("Maximum"),
        help_text=_(
            "The value that means 'done'. Leave 100 to show the value as a percentage; "
            "any other number shows 'value / maximum'."
        ),
    )

    class Meta:
        icon = "order"
        label = _("Progress bar")
        template = "cms/blocks/progress.html"

    def clean(self, value: Any) -> Any:
        cleaned = super().clean(value)
        if cleaned["value"] > cleaned["max"]:
            raise StructBlockValidationError(
                block_errors={
                    "value": ValidationError(_("The value cannot be more than the maximum."))
                }
            )
        return cleaned

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        context["progress_id"] = _unique_id(parent_context, "progress", value)
        return context


def validate_video_url(url: str) -> None:
    """Only YouTube and Vimeo addresses (https) whose no-tracking embed origin is in
    `settings.MPMV_VIDEO_EMBED_ORIGINS`, the same list the CSP `frame-src` is built from."""
    links = parse_video_url(url)
    allowed = {o.rstrip("/") for o in settings.MPMV_VIDEO_EMBED_ORIGINS}
    embed = urlsplit(links.embed_url) if links else None
    if links is None or f"{embed.scheme}://{embed.netloc}" not in allowed:  # type: ignore[union-attr]
        raise ValidationError(
            _(
                "Use the address of a YouTube or Vimeo video, starting with https://. "
                "Example: https://www.youtube.com/watch?v=abcdefghijk or https://vimeo.com/123456789."
            )
        )


class VideoModalBlock(blocks.StructBlock):  # type: ignore[misc]
    """A poster with a play button that opens the video in a popup (THM.14). The video loads
    only after the visitor accepts marketing cookies (consent gate); until then, and without
    JavaScript, the button is a link to the video. The provider's origin must also be allowed
    by the CSP `frame-src` (an operations setting, docs/dev/themes.md) for the frame to show."""

    title = blocks.CharBlock(
        max_length=120,
        help_text=_("The name of the video, shown in the popup. Up to 120 characters. Required."),
    )
    poster = ImageChooserBlock(
        required=False,
        label=_("Cover picture"),
        help_text=_(
            "A picture shown before anyone plays the video. If you leave it empty, "
            "only the play button is shown."
        ),
    )
    alt_text = blocks.CharBlock(
        required=False,
        max_length=250,
        help_text=_(
            "Describe the cover picture in a few words, for people who cannot see it. "
            "Required when you pick a cover picture."
        ),
    )
    url = blocks.URLBlock(
        validators=[validate_video_url],
        label=_("Video address"),
        help_text=_(
            "The address of a YouTube or Vimeo video, starting with https://. "
            "Other sites are not accepted."
        ),
    )

    class Meta:
        icon = "media"
        label = _("Video")
        template = "cms/blocks/video_modal.html"

    def clean(self, value: Any) -> Any:
        cleaned = super().clean(value)
        if cleaned.get("poster") and not (cleaned.get("alt_text") or "").strip():
            raise StructBlockValidationError(
                block_errors={
                    "alt_text": ValidationError(_("Describe the cover picture: this is required."))
                }
            )
        return cleaned

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        links = parse_video_url(value["url"])
        request = (parent_context or {}).get("request")
        if links is not None and request is not None:
            request.mpmv_video_embed = True  # the CSP middleware adds the frame origins
        context["video"] = links
        context["video_id"] = _unique_id(parent_context, "video", value)
        return context


# --- Download (CMS.15)


@dataclass(frozen=True)
class Download:
    """What the download template shows: the link and the words that go with it."""

    url: str
    title: str
    type_label: str
    size_label: str

    @property
    def details(self) -> str:
        """ "PDF, 1.2 MB": the type and size, in text, before the click."""
        return ", ".join(part for part in (self.type_label, self.size_label) if part)


def download_for(document: Any, title: str, language: str) -> Download | None:
    """The download of `document` with its size in `language`'s number format, or None when the
    document or its file is missing (the block is then hidden, never a broken link)."""
    if document is None:
        return None
    try:
        size = int(document.file.size)
        url = str(document.url)
    except (OSError, ValueError):  # the file is gone from the storage
        return None
    value, unit = file_size_parts(size)
    with translation.override(language):
        number = number_format(value, decimal_pos=0 if unit == "B" else 1, use_l10n=True)
    return Download(
        url=url,
        title=(title or "").strip() or str(document.title),
        type_label=file_type_label(str(document.filename)),
        size_label=f"{number} {unit}",
    )


class DownloadBlock(blocks.StructBlock):  # type: ignore[misc]
    """A link to a file of the media library that shows its type and size before the click
    (D-36, CMS.15). Type and size are read from the file, so an editor cannot get them wrong; if
    the document is deleted or its file is gone, nothing is shown."""

    document = DocumentChooserBlock(
        label=_("File"),
        help_text=_(
            "Choose a file from the media library, or upload a new one. Its type and size are "
            "shown next to the link by themselves."
        ),
    )
    title = blocks.CharBlock(
        required=False,
        max_length=120,
        help_text=_(
            "The words of the link, for example 'Terms of sale'. Up to 120 characters. "
            "If you leave it empty, the title of the file is used."
        ),
    )

    class Meta:
        icon = "doc-full-inverse"
        label = _("Download")
        template = "cms/blocks/download.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        context["download"] = download_for(
            value.get("document"), value.get("title", ""), _page_language(parent_context)
        )
        return context


class ContentSnippetBlock(blocks.StructBlock):  # type: ignore[misc]
    """A reusable content snippet placed in a page body (D-36, CMS.14). The text is shown in the
    page's language: the snippet's own live translation, else the primary language's live copy
    (spec 09's explicit fallback), never a third language; an unpublished snippet renders
    nothing. The snippet is rich text only, so it can never contain this block."""

    snippet = SnippetChooserBlock(
        "cms.ContentSnippet",
        label=_("Snippet"),
        help_text=_(
            "Choose a reusable text. When it is changed and approved, every page that uses it "
            "shows the new text. Nothing is shown while the snippet is not published."
        ),
    )

    class Meta:
        icon = "snippet"
        label = _("Reusable snippet")
        template = "cms/blocks/content_snippet.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        from . import services

        context = super().get_context(value, parent_context=parent_context)
        page = (parent_context or {}).get("page")
        context["content_snippet"] = services.live_snippet_copy(
            value.get("snippet"), _page_language(parent_context), memo=page
        )
        return context


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

    heading = blocks.CharBlock(
        required=False,
        max_length=120,
        help_text=_(
            "A title above the products, up to 120 characters. "
            "If you leave it empty, the title is the name of the chosen category, tag, brand or "
            "shop."
        ),
    )
    source = blocks.StreamBlock(
        [
            (
                "category",
                CategoryChooserBlock(
                    label=_("Category"),
                    help_text=_("Show the newest products of this category."),
                ),
            ),
            (
                "term",
                TermChooserBlock(
                    label=_("Term"),
                    help_text=_("Show the newest products of this category or tag."),
                ),
            ),
            (
                "brand",
                BrandChooserBlock(
                    label=_("Brand"), help_text=_("Show the newest products of this brand.")
                ),
            ),
            (
                "vendor",
                VendorChooserBlock(
                    label=_("Vendor"), help_text=_("Show the newest products of this shop.")
                ),
            ),
        ],
        min_num=1,
        max_num=1,
        label=_("Products of"),
        help_text=_("Choose where the products come from: one category, tag, brand or shop."),
    )
    limit = blocks.IntegerBlock(
        min_value=1,
        max_value=PRODUCT_GRID_MAX,
        default=PRODUCT_GRID_DEFAULT,
        label=_("Number of products"),
        help_text=_(
            "How many products to show, from 1 up to the maximum allowed. "
            "If you leave it empty, the default number is used."
        ),
    )
    see_all = blocks.BooleanBlock(
        required=False,
        default=True,
        label=_("Link to the full listing"),
        help_text=_("Tick to add a 'See all' link under the products, to the full list."),
    )

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
PRODUCT_CARDS_MAX = 12  # one listing row of a page; more is the product grid's job


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
        max_num=PRODUCT_CARDS_MAX,
        label=_("Products"),
        help_text=_(
            "The products to show, in this order. Add at least one and at most 12. A product "
            "that is no longer for sale, or has no text in the page language, is left out by "
            "itself."
        ),
    )

    class Meta:
        icon = "pick"
        label = _("Product cards")
        template = "cms/blocks/product_cards.html"

    def _cards(self, value: Any, parent_context: Mapping[str, Any] | None) -> list[ProductCard]:
        """The cards, computed once per block value and request: the page render and the
        SEO.7 graph both ask for them."""
        language = _page_language(dict(parent_context) if parent_context else None)
        request = (parent_context or {}).get("request")
        cache: dict[tuple[int, str], tuple[Any, list[ProductCard]]] | None = None
        if request is not None:
            cache = request.__dict__.setdefault("_product_cards_cache", {})
            hit = cache.get((id(value), language))
            if hit is not None and hit[0] is value:
                return hit[1]
        cards = product_cards(_shown_in_language(value["products"], language), language)
        if cache is not None:
            cache[(id(value), language)] = (value, cards)
        return cards

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
    return business_tz()


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
        help_text=_(
            "When the launch happens, in the platform's local time. Example: 2026-12-01 09:00. "
            "Required: the countdown cannot run without it."
        ),
    )
    heading = blocks.CharBlock(
        max_length=120,
        help_text=_(
            "The title above the countdown, for example 'New collection'. Up to 120 characters. "
            "Required."
        ),
    )
    body = blocks.TextBlock(
        required=False,
        max_length=400,
        help_text=_(
            "A sentence or two about the launch, up to 400 characters. "
            "If you leave it empty, only the title and the clock are shown."
        ),
    )
    product = ProductChooserBlock(
        required=False,
        help_text=_(
            "The product being launched. The buttons lead to it. Choose a product or a category, "
            "not both."
        ),
    )
    category = CategoryChooserBlock(
        required=False,
        help_text=_(
            "The category being launched. The buttons lead to it. Choose a product or a category, "
            "not both."
        ),
    )
    cta_label = blocks.CharBlock(
        required=False,
        max_length=60,
        label=_("Button text"),
        help_text=_(
            "The words on the button before the launch, for example 'Get notified'. "
            "If you leave it empty, there is no button."
        ),
    )
    cta_link = blocks.URLBlock(
        required=False,
        label=_("Button link"),
        help_text=_(
            "Where the button leads, for example 'https://example.com/launch'. "
            "If you leave it empty, it leads to the chosen product or category."
        ),
    )
    expired_text = blocks.CharBlock(
        required=False,
        max_length=120,
        label=_("Text after the launch"),
        help_text=_(
            "The words shown once the launch has passed, up to 120 characters. "
            "If you leave it empty, visitors read 'Available now'."
        ),
    )
    expired_link = blocks.URLBlock(
        required=False,
        label=_("Link after the launch"),
        help_text=_(
            "Where the link leads once the launch has passed. "
            "If you leave it empty, it leads to the chosen product or category."
        ),
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

"""CMS.2 (part 2/2): the chooser cards of a page body (spec 10 §10.2, spec 03 "URLs").

Done when: "An editor builds the home page without code". A "featured links" block holds cards
that point to a page, a catalog taxonomy term or a vendor storefront, chosen in the admin (no
hand-typed URLs). Term and storefront URLs follow spec 03: `/{taxonomy}/{term}/{child}/` and
`/shop/{vendor}/`, prefixed by `/<code>` outside the primary language (D-07 proposal).
"""

from wagtail import blocks

from apps.catalog.models import Term
from apps.cms.blocks import (
    FeaturedLinksBlock,
    PageCardBlock,
    TermCardBlock,
    TermChooserBlock,
    VendorCardBlock,
    VendorChooserBlock,
)
from apps.cms.engine import link_language, localized_path, storefront_path, term_path
from apps.cms.models import body_field
from apps.vendors.models import Vendor

# --- URLs (pure)


def test_a_path_in_the_primary_language_has_no_prefix() -> None:
    assert localized_path("en", "en", ["shop", "pottery"]) == "/shop/pottery/"


def test_a_path_in_another_language_is_prefixed_with_its_code() -> None:
    assert localized_path("it", "en", ["shop", "pottery"]) == "/it/shop/pottery/"


def test_a_storefront_lives_under_shop() -> None:
    assert storefront_path("pottery", "en", "en") == "/shop/pottery/"
    assert storefront_path("pottery", "de", "en") == "/de/shop/pottery/"


def test_a_term_path_is_its_taxonomy_then_its_ancestors_then_itself() -> None:
    assert term_path("colour", ["blue"], "en", "en") == "/colour/blue/"
    assert term_path("marca", ["acme", "modello-1"], "it", "en") == "/it/marca/acme/modello-1/"


def test_a_link_is_in_the_requested_language_when_every_segment_has_it() -> None:
    assert link_language("it", "en", [{"en", "it"}, {"it"}]) == "it"


def test_a_link_falls_back_to_the_primary_language_when_one_segment_misses_a_translation() -> None:
    assert link_language("it", "en", [{"en", "it"}, {"en"}]) == "en"


def test_there_is_no_link_when_no_language_covers_every_segment() -> None:
    assert link_language("it", "en", [{"en"}, {"it"}]) is None
    assert link_language("it", "en", [{"en"}, set()]) is None


# --- Blocks


def test_the_page_body_offers_featured_links() -> None:
    children = body_field().stream_block.child_blocks
    assert isinstance(children["featured"], FeaturedLinksBlock)


def test_a_featured_links_block_holds_page_term_and_vendor_cards() -> None:
    block = FeaturedLinksBlock()
    assert not block.child_blocks["heading"].required
    cards = block.child_blocks["cards"]
    assert isinstance(cards, blocks.StreamBlock)
    assert isinstance(cards.child_blocks["page"], PageCardBlock)
    assert isinstance(cards.child_blocks["term"], TermCardBlock)
    assert isinstance(cards.child_blocks["vendor"], VendorCardBlock)
    assert cards.meta.min_num == 1


def test_cards_choose_their_target_and_may_relabel_it() -> None:
    page = PageCardBlock().child_blocks
    assert isinstance(page["page"], blocks.PageChooserBlock)
    term = TermCardBlock().child_blocks
    assert isinstance(term["term"], TermChooserBlock)
    assert term["term"].target_model is Term
    vendor = VendorCardBlock().child_blocks
    assert isinstance(vendor["vendor"], VendorChooserBlock)
    assert vendor["vendor"].target_model is Vendor
    for children in (page, term, vendor):
        assert not children["label"].required
        assert not children["text"].required


def test_chooser_blocks_deconstruct_to_an_importable_path() -> None:
    """Migrations reference the blocks by path: generated classes must live in apps.cms.blocks."""
    assert TermChooserBlock().deconstruct()[0] == "apps.cms.blocks.TermChooserBlock"
    assert VendorChooserBlock().deconstruct()[0] == "apps.cms.blocks.VendorChooserBlock"

"""CAT.9: the pure side of product search (spec 03 §3.1 and §3.2): query normalisation, length
limits, the text search configuration of each language, and the ranking of the hits."""

from django.conf import settings

from apps.catalog import engine
from apps.catalog.engine import SearchHit

# --- Query normalisation and limits


def test_the_query_is_trimmed_and_its_inner_whitespace_collapsed() -> None:
    terms = engine.normalise_search_query("  leather \t  bag \n")
    assert terms.text == "leather bag"
    assert terms.error is None


def test_the_sku_key_is_the_match_key_of_the_query() -> None:
    # `bag 100` finds `BAG  100` (CAT.9 done-when): both sides go through sku_match_key.
    assert engine.normalise_search_query("BAG  100").sku_key == "bag 100"
    assert engine.normalise_search_query("bag 100").sku_key == engine.sku_match_key("BAG  100")


def test_a_sku_prefix_needs_at_least_four_characters() -> None:
    assert engine.SKU_PREFIX_MIN_LENGTH == 4
    assert engine.normalise_search_query("ab-1").sku_prefix is True
    assert engine.normalise_search_query("ab1").sku_prefix is False
    # A short query can still match a whole SKU exactly.
    assert engine.normalise_search_query("ab1").sku_key == "ab1"


def test_an_empty_or_too_short_query_searches_nothing() -> None:
    for raw in ("", "   ", "a", "\x00\x01"):
        terms = engine.normalise_search_query(raw)
        assert terms.error is not None
        assert terms.text == ""
        assert terms.sku_key is None


def test_control_characters_are_dropped() -> None:
    assert engine.normalise_search_query("bag\x00 100\x1f").text == "bag 100"


def test_a_long_query_is_cut_at_the_length_limit_on_a_word_boundary() -> None:
    raw = " ".join(["leather"] * 40)
    terms = engine.normalise_search_query(raw)
    assert len(terms.text) <= engine.SEARCH_MAX_LENGTH
    assert terms.text.split() == ["leather"] * len(terms.text.split())
    assert terms.error is None


def test_a_query_keeps_at_most_the_maximum_number_of_words() -> None:
    terms = engine.normalise_search_query("a1 b2 c3 d4 e5 f6 g7 h8 i9 j10 k11")
    assert len(terms.text.split()) == engine.SEARCH_MAX_WORDS


def test_a_query_longer_than_any_sku_has_no_sku_key() -> None:
    terms = engine.normalise_search_query("x" * 90)
    assert terms.text
    assert terms.sku_key is None


def test_the_query_is_unicode_normalised() -> None:
    # NFKC: a full-width SKU typed on a CJK keyboard matches the ASCII one.
    assert engine.normalise_search_query("\uff22\uff21\uff27\uff11\uff10\uff10").sku_key == "bag100"


# --- Text search configuration per language


def test_each_site_language_has_a_text_search_configuration() -> None:
    for code, _name in settings.LANGUAGES:
        assert engine.search_config(code) != engine.SEARCH_FALLBACK_CONFIG, code


def test_the_text_search_configurations_are_the_postgresql_ones() -> None:
    assert engine.search_config("en") == "english"
    assert engine.search_config("it") == "italian"
    assert engine.search_config("de") == "german"
    assert engine.search_config("fr") == "french"
    assert engine.search_config("es") == "spanish"


def test_an_unknown_language_falls_back_to_the_simple_configuration() -> None:
    assert engine.search_config("xx") == engine.SEARCH_FALLBACK_CONFIG == "simple"


# --- Ranking


def test_an_exact_sku_match_comes_first_then_a_sku_prefix_then_text() -> None:
    hits = [
        SearchHit(product_id=1, text_rank=0.9, similarity=0.9),
        SearchHit(product_id=2, sku_prefix=True),
        SearchHit(product_id=3, sku_exact=True),
    ]
    assert engine.rank_search_hits(hits) == [3, 2, 1]


def test_text_hits_are_ordered_by_their_combined_score() -> None:
    hits = [
        SearchHit(product_id=1, similarity=0.7),
        SearchHit(product_id=2, text_rank=0.5, similarity=0.6),
        SearchHit(product_id=3, text_rank=0.2),
    ]
    assert engine.rank_search_hits(hits) == [2, 1, 3]


def test_hits_of_one_product_are_merged() -> None:
    # A product found by its SKU, a variant SKU and its title appears once, with its best signals.
    hits = [
        SearchHit(product_id=7, text_rank=0.1),
        SearchHit(product_id=7, sku_prefix=True),
        SearchHit(product_id=8, text_rank=0.3),
        SearchHit(product_id=7, similarity=0.4),
    ]
    assert engine.rank_search_hits(hits) == [7, 8]


def test_equal_scores_put_the_newest_product_first() -> None:
    hits = [SearchHit(product_id=4, text_rank=0.5), SearchHit(product_id=9, text_rank=0.5)]
    assert engine.rank_search_hits(hits) == [9, 4]


def test_the_ranking_is_cut_at_the_limit() -> None:
    hits = [SearchHit(product_id=n, text_rank=n / 100) for n in range(1, 11)]
    assert engine.rank_search_hits(hits, limit=3) == [10, 9, 8]

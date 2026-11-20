"""SEO.8 2/2: the page meta fallback chain (D-36), pure."""

from apps.seo.meta_engine import resolve_meta


def test_the_seo_title_beats_the_page_title() -> None:
    assert resolve_meta(page_title="About", seo_title=" Who we are ").title == "Who we are"
    assert resolve_meta(page_title="About", seo_title="  ").title == "About"


def test_the_description_falls_back_to_the_summary() -> None:
    assert (
        resolve_meta(page_title="x", search_description="Own", summary="Sum").description == "Own"
    )
    assert resolve_meta(page_title="x", summary="Sum").description == "Sum"
    assert resolve_meta(page_title="x").description == ""


def test_the_image_chain_is_page_then_main_then_site_then_operator() -> None:
    chain = {
        "page_seo_image": "a",
        "page_image": "b",
        "site_image": "c",
        "operator_image": "d",
    }
    for expected in ("a", "b", "c", "d"):
        assert resolve_meta(page_title="x", **chain).image == expected
        chain.pop(
            {"a": "page_seo_image", "b": "page_image", "c": "site_image"}.get(expected, "x"), None
        )
    assert resolve_meta(page_title="x").image == ""

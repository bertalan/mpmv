"""OPS.12: the pure helpers of the temporary staff "Site links" page."""

from apps.seo import site_links_engine as engine


def test_a_section_caps_its_links_and_counts_the_rest() -> None:
    links = [engine.Link(label=f"Item {n}", path=f"/item-{n}/") for n in range(7)]
    section = engine.section("Items", links, limit=5)
    assert [link.label for link in section.links] == [f"Item {n}" for n in range(5)]
    assert section.more == 2


def test_a_section_without_overflow_has_no_more() -> None:
    section = engine.section("Items", [engine.Link(label="One", path="/one/")])
    assert section.more == 0


def test_a_section_takes_a_known_total_when_only_the_first_rows_were_built() -> None:
    links = [engine.Link(label="One", path="/one/")]
    assert engine.section("Items", links, total=60, limit=50).more == 10


def test_routed_links_keep_the_routes_that_resolve_in_order() -> None:
    known = {"home": "/", "login": "/account/login/"}
    links = engine.routed_links(
        [("Home", "home"), ("Gone", "missing"), ("Sign in", "login")], known.get
    )
    assert links == (
        engine.Link(label="Home", path="/"),
        engine.Link(label="Sign in", path="/account/login/"),
    )


def test_tree_links_indent_by_depth_and_drop_unroutable_pages() -> None:
    rows = [("Home", "/", 2), ("About", "/about/", 3), ("Hidden", None, 3), ("Team", "/a/t/", 4)]
    assert engine.tree_links(rows, base_depth=2) == (
        engine.Link(label="Home", path="/", depth=0),
        engine.Link(label="About", path="/about/", depth=1),
        engine.Link(label="Team", path="/a/t/", depth=2),
    )

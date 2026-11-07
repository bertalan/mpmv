"""THM.1: the pure theme functions (spec 14 §14.2, §14.3): manifest, chain, candidates, choice."""

from typing import Any

import pytest

from apps.themes.engine import (
    BASE_THEME,
    ThemeError,
    ThemeManifest,
    build_registry,
    is_themable,
    parse_manifest,
    pick_theme,
    template_candidates,
    theme_chain,
)


def manifest_data(slug: str = "ride", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "slug": slug,
        "name": f"{slug.title()} theme",
        "version": "1.0.0",
        "description": "A test theme.",
        "direction": "ltr",
    }
    if slug != BASE_THEME:
        data["parent"] = BASE_THEME
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def manifest(slug: str, parent: str | None = BASE_THEME) -> ThemeManifest:
    return ThemeManifest(slug=slug, name=slug.title(), version="1", parent=parent)


def registry_of(*manifests: ThemeManifest) -> dict[str, ThemeManifest]:
    return {m.slug: m for m in manifests}


BASE = manifest(BASE_THEME, parent=None)


# --- Manifest


def test_a_valid_child_manifest_is_parsed() -> None:
    parsed = parse_manifest(manifest_data("ride"), dirname="ride")
    assert parsed == ThemeManifest(
        slug="ride",
        name="Ride theme",
        version="1.0.0",
        parent="base",
        description="A test theme.",
        direction="ltr",
    )


def test_base_has_no_parent_and_description_and_direction_have_defaults() -> None:
    data = manifest_data("base", description=None, direction=None)
    parsed = parse_manifest(data, dirname="base")
    assert parsed.parent is None
    assert (parsed.description, parsed.direction) == ("", "ltr")


@pytest.mark.parametrize(
    ("data", "dirname", "message"),
    [
        (manifest_data("ride"), "other", "directory"),
        (manifest_data("ride", name=None), "ride", "name"),
        (manifest_data("ride", name=""), "ride", "name"),
        (manifest_data("ride", version=None), "ride", "version"),
        (manifest_data("ride", version=2), "ride", "version"),
        (manifest_data("ride", parent=None), "ride", "parent"),
        (manifest_data("base", parent="ride"), "base", "parent"),
        (manifest_data("ride", direction="rtl"), "ride", "direction"),
        (manifest_data("ride", colour="red"), "ride", "colour"),
        (manifest_data("Ride!"), "Ride!", "slug"),
        (manifest_data("ride", slug=None), "ride", "slug"),
    ],
)
def test_an_invalid_manifest_is_rejected_with_the_reason(
    data: dict[str, Any], dirname: str, message: str
) -> None:
    with pytest.raises(ThemeError, match=message):
        parse_manifest(data, dirname=dirname)


# --- Chain


def test_a_child_chain_ends_in_base() -> None:
    registry = registry_of(BASE, manifest("ride"), manifest("ride-dark", parent="ride"))
    assert theme_chain("ride-dark", registry) == ["ride-dark", "ride", "base"]
    assert theme_chain("ride", registry) == ["ride", "base"]
    assert theme_chain("base", registry) == ["base"]


def test_an_unknown_theme_has_no_chain() -> None:
    with pytest.raises(ThemeError, match="unknown theme 'nope'"):
        theme_chain("nope", registry_of(BASE))


def test_an_unknown_parent_breaks_the_chain() -> None:
    with pytest.raises(ThemeError, match="unknown theme 'missing'"):
        theme_chain("ride", registry_of(BASE, manifest("ride", parent="missing")))


def test_a_cycle_breaks_the_chain() -> None:
    registry = registry_of(BASE, manifest("a", parent="b"), manifest("b", parent="a"))
    with pytest.raises(ThemeError, match="cycle"):
        theme_chain("a", registry)


def test_a_chain_not_ending_in_base_is_rejected() -> None:
    with pytest.raises(ThemeError, match="base"):
        theme_chain("orphan", registry_of(BASE, manifest("orphan", parent=None)))


# --- Registry


def test_the_registry_keeps_valid_themes_and_reports_the_others() -> None:
    registry = build_registry(
        {
            "base": manifest_data("base"),
            "ride": manifest_data("ride"),
            "broken": manifest_data("broken", name=""),
            "lost": manifest_data("lost", parent="missing"),
            "unreadable": ThemeError("theme.toml is not valid TOML"),
        }
    )
    assert list(registry.manifests) == ["base", "ride"]
    assert registry.chain("ride") == ("ride", "base")
    assert "ride" in registry
    assert "lost" not in registry
    assert registry.choices() == [("base", "Base theme"), ("ride", "Ride theme")]
    joined = "\n".join(registry.errors)
    for slug in ("broken", "lost", "unreadable"):
        assert f"themes/{slug}" in joined


def test_a_registry_without_base_reports_it() -> None:
    registry = build_registry({"ride": manifest_data("ride")})
    assert list(registry.manifests) == []
    assert any("base" in error for error in registry.errors)


# --- Template candidates


def test_candidates_follow_the_chain_from_child_to_base() -> None:
    assert template_candidates("cms/home_page.html", ["ride", "base"]) == [
        "themes/ride/templates/cms/home_page.html",
        "themes/base/templates/cms/home_page.html",
    ]


def test_candidates_use_the_given_root() -> None:
    assert template_candidates("layouts/base.html", ["base"], root="/srv/themes") == [
        "/srv/themes/base/templates/layouts/base.html"
    ]


@pytest.mark.parametrize(
    "name",
    [
        "../theme.toml",
        "cms/../../ride/theme.toml",
        "/etc/passwd",
        "",
        "admin/base_site.html",
        "wagtailadmin/base.html",
        "wagtailusers/users/index.html",
        "wagtailsnippets/snippets/index.html",
    ],
)
def test_admin_templates_and_unsafe_names_are_never_themed(name: str) -> None:
    assert not is_themable(name)
    assert template_candidates(name, ["ride", "base"]) == []


@pytest.mark.parametrize("name", ["cms/home_page.html", "layouts/base.html", "core/base.html"])
def test_public_templates_are_themed(name: str) -> None:
    assert is_themable(name)


# --- Selection: Wagtail Site setting, then MPMV_THEME_DEFAULT, then base


def test_the_site_choice_wins() -> None:
    registry = registry_of(BASE, manifest("ride"), manifest("calm"))
    assert pick_theme("ride", "calm", registry) == "ride"


@pytest.mark.parametrize("site_choice", ["", "removed"])
def test_no_or_an_unknown_site_choice_falls_back_to_the_environment_default(
    site_choice: str,
) -> None:
    registry = registry_of(BASE, manifest("calm"))
    assert pick_theme(site_choice, "calm", registry) == "calm"


@pytest.mark.parametrize("env_default", ["", "removed"])
def test_then_to_base(env_default: str) -> None:
    assert pick_theme("", env_default, registry_of(BASE)) == "base"


def test_no_base_theme_is_an_error() -> None:
    with pytest.raises(ThemeError, match="base"):
        pick_theme("", "", {})

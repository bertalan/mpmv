"""THM.1: the theme-aware template loader (spec 14 §14.3).

The active theme is looked up first, then each parent up to `base`, then the app template
directories. The loader caches by `(theme, template name)`, so one theme's template is never
served to a site using another theme.
"""

from pathlib import Path
from typing import Any, cast

import pytest
from django.conf import settings as django_settings
from django.template import Context, Engine, TemplateDoesNotExist
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.loaders import ThemeLoader
from apps.themes.services import current_chain, use_theme
from apps.themes.tests.builders import write_theme

LOADER = "apps.themes.loaders.ThemeLoader"


@pytest.fixture
def themes(tmp_path: Path, settings: SettingsWrapper) -> Path:
    root = tmp_path / "themes"
    write_theme(
        root,
        "base",
        templates={
            "page.html": "base page",
            "only_base.html": "from base",
            "wrapped.html": "[{% block body %}base body{% endblock %}]",
            "admin/base_site.html": "themed admin",
        },
    )
    write_theme(
        root,
        "alt",
        templates={
            "page.html": "alt page",
            "wrapped.html": ('{% extends "wrapped.html" %}{% block body %}alt body{% endblock %}'),
        },
    )
    write_theme(root, "calm", templates={"page.html": "calm page"})
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    return root


def render(engine: Engine, name: str) -> str:
    return engine.get_template(name).render(Context())


@pytest.fixture
def engine(themes: Path, tmp_path: Path) -> Engine:
    app_dir = tmp_path / "app_templates"
    app_dir.mkdir()
    (app_dir / "page.html").write_text("app page", encoding="utf-8")
    (app_dir / "app_only.html").write_text("from the app", encoding="utf-8")
    (app_dir / "admin").mkdir()
    (app_dir / "admin" / "base_site.html").write_text("plain admin", encoding="utf-8")
    return Engine(
        dirs=[str(app_dir)], loaders=[LOADER, "django.template.loaders.filesystem.Loader"]
    )


def test_the_active_theme_wins(engine: Engine) -> None:
    with use_theme("alt"):
        assert render(engine, "page.html") == "alt page"
    with use_theme("calm"):
        assert render(engine, "page.html") == "calm page"


def test_a_template_missing_in_the_child_comes_from_base(engine: Engine) -> None:
    with use_theme("alt"):
        assert render(engine, "only_base.html") == "from base"


def test_a_template_in_no_theme_comes_from_the_app_directories(engine: Engine) -> None:
    with use_theme("alt"):
        assert render(engine, "app_only.html") == "from the app"


def test_a_child_template_can_extend_the_parent_version_of_itself(engine: Engine) -> None:
    with use_theme("alt"):
        assert render(engine, "wrapped.html") == "[alt body]"
    with use_theme("base"):
        assert render(engine, "wrapped.html") == "[base body]"


def test_admin_templates_are_never_themed(engine: Engine) -> None:
    with use_theme("base"):
        assert render(engine, "admin/base_site.html") == "plain admin"


def test_without_an_active_request_the_environment_default_is_used(
    engine: Engine, settings: SettingsWrapper
) -> None:
    assert current_chain() == ("base",)
    assert render(engine, "page.html") == "base page"
    settings.MPMV_THEME_DEFAULT = "calm"
    assert current_chain() == ("calm", "base")
    assert render(engine, "page.html") == "calm page"


def test_the_cache_is_keyed_by_theme_and_name(engine: Engine) -> None:
    for slug in ("alt", "calm", "alt", "calm"):
        with use_theme(slug):
            assert render(engine, "page.html") == f"{slug} page"
    loader = engine.template_loaders[0]
    assert isinstance(loader, ThemeLoader)
    cached = {key[1:3] for key in loader.template_cache}
    assert {("alt", "page.html"), ("calm", "page.html")} <= cached


def test_a_cached_template_is_reused_and_reset_clears_it(engine: Engine, themes: Path) -> None:
    with use_theme("alt"):
        first = engine.get_template("page.html")
        assert engine.get_template("page.html") is first
        (themes / "alt" / "templates" / "page.html").write_text("edited", encoding="utf-8")
        assert render(engine, "page.html") == "alt page"
        engine.template_loaders[0].reset()
        assert render(engine, "page.html") == "edited"


def test_a_missing_template_raises(engine: Engine) -> None:
    with use_theme("alt"), pytest.raises(TemplateDoesNotExist):
        engine.get_template("nowhere.html")


def test_use_theme_rejects_an_unknown_theme(themes: Path) -> None:
    with pytest.raises(LookupError), use_theme("nope"):
        pass


def test_the_project_engine_puts_the_theme_loader_first_and_uncached() -> None:
    (engine,) = django_settings.TEMPLATES
    options = cast("dict[str, Any]", engine["OPTIONS"])
    loaders: list[Any] = options["loaders"]
    assert loaders[0] == LOADER
    # Django's cached.Loader keys by name only: it must never wrap the theme loader.
    assert LOADER not in repr(loaders[1:])


def test_the_dev_autoreloader_watches_the_themes_directory(themes: Path) -> None:
    """THM.1 review: without `get_dirs` a theme template edit never resets the loader cache."""
    from django.template.autoreload import get_template_directories

    assert themes.resolve() in {path.resolve() for path in get_template_directories()}

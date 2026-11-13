"""SEO.9 and SEO.5: consent.js is lazy; the page never links it and base.js imports it on need."""

from pathlib import Path

from django.template import Context, Template

ROOT = Path(__file__).resolve().parents[4]
JS = ROOT / "themes" / "base" / "static" / "themes" / "base" / "js"


def test_the_layout_ships_no_consent_script_tag() -> None:
    layout = (ROOT / "themes" / "base" / "templates" / "layouts" / "base.html").read_text()
    assert "consent.js" not in layout.replace("hidden until consent.js", "")


def test_consent_js_lives_in_the_lazy_directory() -> None:
    assert (JS / "lazy" / "consent.js").is_file()
    assert not (JS / "consent.js").exists()


def test_the_loader_imports_consent_js_only_without_a_decision_or_with_held_tags() -> None:
    loader = (JS / "base.js").read_text()
    assert "import(script)" in loader
    assert "!document.cookie.includes(" in loader
    assert "data-consent-banner" in loader


def test_the_banner_names_the_lazy_script_the_loader_imports() -> None:
    html = Template("{% consent_banner %}").render(Context({}))
    assert "data-script=" in html
    assert "js/lazy/consent" in html

"""CMS.1: only Wagtail's date and time picker scripts get the CSP nonce in the admin."""

import pytest

from apps.cms.csp import nonce_picker_scripts

NONCE = "n0nce"

PICKERS = [
    '<script>initDateChooser("id_from", {"dayOfWeekStart": 0, "format": "Y-m-d"});</script>',
    '<script>initTimeChooser("id_at", {"format": "H:i"});</script>',
    '<script>initDateTimeChooser("id_go_live_at", {"format": "Y-m-d H:i"});</script>',
    # I18N.3: the main image field of a page (Wagtail's image chooser widget).
    '<script>new ImageChooser("id_image", {"modalUrl": "/admin/images/chooser/"});</script>',
    '<script>new DocumentChooser("id_file", {"modalUrl": "/admin/documents/chooser/"});</script>',
    # CMS.3: the author of a blog article (Wagtail's snippet chooser widget).
    '<script>new SnippetChooser("id_author", {"modalUrl": "/admin/snippets/choose/"});</script>',
    # CMS.4: the term and vendor of a landing page (a generic chooser viewset's widget).
    '<script>new Chooser("id_term", {"modalUrl": "/admin/cms/term-chooser/"});</script>',
]


@pytest.mark.parametrize("script", PICKERS)
def test_picker_initialisers_get_the_nonce(script: str) -> None:
    html, changed = nonce_picker_scripts(f"<p>{script}</p>", NONCE)
    assert changed == 1
    assert html == "<p>" + script.replace("<script>", f'<script nonce="{NONCE}">') + "</p>"


@pytest.mark.parametrize(
    "script",
    [
        "<script>alert(1)</script>",
        '<script>initDateChooser("a", {});alert(1);</script>',
        '<script>initDateChooser("a", {});alert({});</script>',
        '<script>initDateChooser("a", {"x": alert(1)});</script>',
        '<script>initDateChooser("a"+alert(1)+"", {});</script>',
        '<script src="/x.js"></script><script>alert(1)</script>',
        "<script>new InlinePanel({});</script>",
        '<script>new ImageChooser("a", {"x": alert(1)});</script>',
        '<script>new ImageChooser("a", {});alert(1);</script>',
        '<script>new Chooser("a", {"x": alert(1)});</script>',
        '<script>new Chooser("a", {});alert(1);</script>',
        '<script>new Function("a", {});</script>',
    ],
)
def test_any_other_inline_script_stays_without_nonce(script: str) -> None:
    assert nonce_picker_scripts(script, NONCE) == (script, 0)


def test_a_picker_next_to_another_script_does_not_lend_it_the_nonce() -> None:
    html, changed = nonce_picker_scripts(
        '<script>initDateChooser("a", {});</script><script>alert(1)</script>', NONCE
    )
    assert changed == 1
    assert html.endswith("</script><script>alert(1)</script>")


def test_pages_without_pickers_are_unchanged() -> None:
    html = '<script src="/static/wagtailadmin/js/core.js"></script><p>Pages</p>'
    assert nonce_picker_scripts(html, NONCE) == (html, 0)

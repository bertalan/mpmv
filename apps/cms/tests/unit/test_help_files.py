"""ARC.36: the help file format, the key encoding and the placeholder allow-list."""

from pathlib import Path

import pytest

from apps.cms import help_files

GOOD = "# Orders\nlast-checked: 2026-11-17\n\nSee **all** orders.\n\n## Status\n\n- one\n"


def write(root: Path, lang: str, kind: str, stem: str, text: str) -> Path:
    path = root / lang / kind / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_route_key_survives_the_file_name_encoding(tmp_path: Path) -> None:
    path = write(tmp_path, "en", "route", "manager__shop__orders", GOOD)
    file = help_files.parse(path, tmp_path)
    assert (file.language, file.kind, file.key) == ("en", "route", "manager:shop:orders")
    assert help_files.key_to_stem("manager:shop:orders") == "manager__shop__orders"
    assert file.title == "Orders"
    assert file.last_checked.isoformat() == "2026-11-17"


def test_a_page_type_keeps_its_case(tmp_path: Path) -> None:
    path = write(tmp_path, "it", "page_type", "cms.BlogPostPage", GOOD)
    assert help_files.parse(path, tmp_path).key == "cms.BlogPostPage"


def test_the_body_becomes_sanitised_rich_text_with_h4_headings() -> None:
    html = help_files.to_rich_text("Hi <script>x()</script> **b**\n\n## Part\n")
    assert "<script" not in html
    assert "<h4>Part</h4>" in html


@pytest.mark.parametrize(
    "text",
    [
        "No title\nlast-checked: 2026-11-17\n",
        "# T\n\nbody",  # no last-checked
        "# T\nlast-checked: yesterday\n",
        "# T\nlast-checked: 2026-11-17\nowner: x\n",
        "# T\nlast-checked: 2026-11-17\n\nCall {{ operator.secret_key }}",
    ],
)
def test_a_malformed_file_or_unknown_placeholder_is_refused(tmp_path: Path, text: str) -> None:
    path = write(tmp_path, "en", "route", "a__b", text)
    with pytest.raises(help_files.HelpFileError):
        help_files.parse(path, tmp_path)


def test_the_general_entry_has_one_fixed_file_name(tmp_path: Path) -> None:
    path = write(tmp_path, "en", "general", "other", GOOD)
    with pytest.raises(help_files.HelpFileError):
        help_files.parse(path, tmp_path)


def test_an_unknown_placeholder_renders_empty_and_is_reported() -> None:
    assert help_files.render_placeholders("a{{ nope.x }}b") == "ab"
    assert help_files.unknown_placeholders("{{ nope.x }} {{ settings.site_name }}") == {"nope.x"}

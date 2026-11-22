"""The text of `llms.txt` (SEO.2, spec 10), pure part: markdown for language-model crawlers,
a title, a one-line summary and link lists per section. No Django, no ORM."""

from collections.abc import Sequence

from apps.core.hidden_text import strip_hidden

from .sitemap_engine import Entry

MAX_LINKS = 50  # per section: the file is a guide to the site, the sitemaps are the full list
MAX_TITLE = 200  # characters of a title or a link text
MAX_SUMMARY = 500  # characters of the one-line summary


def _truncate(text: str, limit: int) -> str:
    """`text` cut on a word boundary, with an ellipsis, so that it is at most `limit` long."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if text[limit - 1] != " " and " " in cut:  # inside a word: drop the word that was cut
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "\u2026"


def _clean(text: str, limit: int = MAX_TITLE) -> str:
    """One line of visible text: no character that would end a markdown link text, no hidden
    character, no HTML (a comment would hide an instruction from people but not from agents)."""
    flat = strip_hidden(text).replace("[", "(").replace("]", ")")
    one_line = " ".join(flat.split())
    return _truncate(one_line, limit).replace("<", "&lt;").replace(">", "&gt;")


def llms_text(title: str, summary: str, sections: Sequence[tuple[str, Sequence[Entry]]]) -> str:
    lines = [f"# {_clean(title)}"]
    if summary.strip():
        lines += ["", f"> {_clean(summary, MAX_SUMMARY)}"]
    for heading, entries in sections:
        shown = [entry for entry in entries if entry.title.strip()][:MAX_LINKS]
        if not shown:
            continue
        lines += ["", f"## {heading}", ""]
        lines += [f"- [{_clean(entry.title)}]({entry.loc})" for entry in shown]
    return "\n".join(lines) + "\n"

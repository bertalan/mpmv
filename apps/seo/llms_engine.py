"""The text of `llms.txt` (SEO.2, spec 10), pure part: markdown for language-model crawlers,
a title, a one-line summary and link lists per section. No Django, no ORM."""

from collections.abc import Sequence

from apps.core.hidden_text import strip_hidden

from .sitemap_engine import Entry

MAX_LINKS = 50  # per section: the file is a guide to the site, the sitemaps are the full list


def _clean(text: str) -> str:
    """One line of visible text: no character that would end a markdown link text, no hidden
    character, no HTML (a comment would hide an instruction from people but not from agents)."""
    flat = strip_hidden(text).replace("[", "(").replace("]", ")")
    return " ".join(flat.replace("<", "&lt;").replace(">", "&gt;").split())


def llms_text(title: str, summary: str, sections: Sequence[tuple[str, Sequence[Entry]]]) -> str:
    lines = [f"# {_clean(title)}"]
    if summary.strip():
        lines += ["", f"> {_clean(summary)}"]
    for heading, entries in sections:
        shown = [entry for entry in entries if entry.title.strip()][:MAX_LINKS]
        if not shown:
            continue
        lines += ["", f"## {heading}", ""]
        lines += [f"- [{_clean(entry.title)}]({entry.loc})" for entry in shown]
    return "\n".join(lines) + "\n"

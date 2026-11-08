"""The theme-aware template loader (THM.1, spec 14 §14.3).

`ThemeLoader` is the first loader of the template engine. For template `name` it looks in
`<MPMV_THEMES_DIR>/<slug>/templates/<name>` for each theme of the active chain (child first,
`base` last); the next loaders (the project and app template directories) answer when no theme
has the file. A child template may `{% extends %}` its own name: the parent theme's (or the
app's) version is found next, as with any loader.

It caches compiled templates under `(themes dir, theme slug, name, skipped origins)`. Django's
`cached.Loader` must never wrap it: that cache is keyed by name only and would serve one theme's
template to a site using another. `get_dirs()` lets the dev autoreloader watch the themes
directory, and `reset()` (which it calls on a template change) clears the cache.
Admin templates are never themed (`engine.is_themable`).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from django.template import Origin, Template, TemplateDoesNotExist
from django.template.loaders.base import Loader

from .engine import is_themable, template_candidates
from .services import current_chain, themes_dir

CacheKey = tuple[str, str, str, tuple[str, ...]]


def _copy_exception(exc: TemplateDoesNotExist) -> TemplateDoesNotExist:
    # Cache a copy without the traceback, which keeps frames (and their locals) alive.
    return TemplateDoesNotExist(exc.args[0] if exc.args else "", tried=exc.tried, chain=exc.chain)


class ThemeLoader(Loader):
    def __init__(self, engine: Any) -> None:
        super().__init__(engine)
        self.template_cache: dict[CacheKey, Template | TemplateDoesNotExist] = {}

    def get_dirs(self) -> list[Path]:
        """What `django.template.autoreload` watches: an edit there resets this loader."""
        return [themes_dir()]

    def get_template_sources(self, template_name: str) -> Iterator[Origin]:
        root = str(themes_dir())
        for path in template_candidates(template_name, list(current_chain()), root=root):
            yield Origin(name=path, template_name=template_name, loader=self)

    def get_contents(self, origin: Origin) -> str:
        try:
            with open(origin.name, encoding=self.engine.file_charset) as fp:
                return fp.read()
        except FileNotFoundError as exc:
            raise TemplateDoesNotExist(origin) from exc

    def get_template(self, template_name: str, skip: list[Origin] | None = None) -> Template:
        if not is_themable(template_name):
            # Answer before resolving the theme: admin pages never query the site setting.
            raise TemplateDoesNotExist(template_name)
        chain = current_chain()
        skipped = tuple(origin.name for origin in skip or () if origin.loader is self)
        key: CacheKey = (str(themes_dir()), chain[0], template_name, skipped)
        cached = self.template_cache.get(key)
        if isinstance(cached, TemplateDoesNotExist):
            raise _copy_exception(cached)
        if cached is not None:
            return cached
        try:
            template = super().get_template(template_name, skip)
        except TemplateDoesNotExist as exc:
            self.template_cache[key] = _copy_exception(exc)
            raise
        self.template_cache[key] = template
        return template

    def reset(self) -> None:
        self.template_cache.clear()

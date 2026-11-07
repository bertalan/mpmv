"""Static files storage of the live image (ARC.5), with Wagtail's admin assets (CMS.1).

WhiteNoise's manifest storage rewrites every `url(...)` in CSS to the hashed file name and stops
`collectstatic` when a referenced file is missing. Wagtail's admin CSS references an image of
`wagtail.images`, an app this project leaves out (see INSTALLED_APPS), so that one
reference is kept as written: nothing in the installed admin uses it. Any other missing file
still fails the build.
"""

from typing import Any

from whitenoise.storage import CompressedManifestStaticFilesStorage

# Files referenced by installed Wagtail assets but shipped by Wagtail apps left out of
# INSTALLED_APPS. Add an entry only for such a file, never for one of our own.
UNINSTALLED_APP_FILES = frozenset(
    {
        "wagtailimages/images/Jcrop.gif",  # wagtailadmin/css/core.css, image cropper
    }
)


class StaticFilesStorage(CompressedManifestStaticFilesStorage):  # type: ignore[misc]
    def hashed_name(self, name: str, content: Any = None, filename: str | None = None) -> str:
        if content is None and self.clean_name(name) in UNINSTALLED_APP_FILES:
            return name
        hashed: str = super().hashed_name(name, content, filename)
        return hashed

"""Field help texts of the help-entry snippet (ARC.32), kept here so the Italian catalog finds them.

`tools/i18n_catalog.py` scans this file (`EXTRA_FILES`); the model imports the constants.
"""

from django.utils.translation import gettext_lazy as _

KIND = _(
    "What the text is for: one screen, every page of one page type, or every screen "
    "that has no text of its own. Example: choose Screen for the brand listing."
)
KEY = _(
    "The name of the screen (for example catalog:brand) or of the page type (for "
    "example cms.StandardPage). Leave it empty for the general text."
)
TITLE = _(
    "The heading of the help text, in a few words that say what the screen is for. "
    "Example: Your orders."
)
BODY = _(
    "What a person can do here and what to do first, in short plain sentences. "
    "Without text the dialog shows only the title."
)
SHOW_VENDOR_GUIDE = _(
    "Tick it on screens for vendors, so the dialog also links the vendor guide. "
    "It has no effect until the platform has a vendor guide address."
)

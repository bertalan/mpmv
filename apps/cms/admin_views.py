"""Editors' account page in the Wagtail admin (CMS.1), without the panels this project lacks.

Wagtail's account page always shows a first and last name form, but our User has `full_name`:
the form's fields are not model fields, so it would demand a name it then discards and block
every other preference from being saved. It also offers a profile picture upload, which no
spec item plans for. Both panels are left out; language, time zone, theme and notification
preferences stay. `admin_urls` mounts this view at the admin's own `account/` path.
"""

from typing import Any

from wagtail.admin.views.account import (
    AccountView,
    AvatarSettingsPanel,
    BaseSettingsPanel,
    NameEmailSettingsPanel,
)

OMITTED_PANELS: tuple[type[BaseSettingsPanel], ...] = (NameEmailSettingsPanel, AvatarSettingsPanel)


class EditorAccountView(AccountView):  # type: ignore[misc]  # Wagtail is untyped
    def get_panels(self) -> list[Any]:
        panels: list[Any] = super().get_panels()
        return [panel for panel in panels if not isinstance(panel, OMITTED_PANELS)]

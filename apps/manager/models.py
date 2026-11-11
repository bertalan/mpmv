"""The dedicated platform-role permissions (CORE.18) hang off one unmanaged model: no table."""

from django.db import models


class PlatformAccess(models.Model):
    """Carries `manager.platform_config|catalog|finance|support`; never instantiated.

    Section checks read role membership from `roles.py`; these permissions exist so a role is
    a group of dedicated permissions, none implied by `is_staff`, Wagtail access or a role.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("platform_config", "Platform role: configuration and tax"),
            ("platform_catalog", "Platform role: catalog and moderation"),
            ("platform_finance", "Platform role: finance and payments"),
            ("platform_support", "Platform role: support"),
        ]

    def __str__(self) -> str:
        return "platform access"

"""Django's admin app with the audited staff site as `admin.site` (SEC.4).

Kept apart from `admin_site` because INSTALLED_APPS imports this module before the models are
ready; the site class is loaded later, from its dotted path, when the admin app is ready.
"""

from django.contrib.admin.apps import AdminConfig


class AuditedAdminConfig(AdminConfig):
    default_site = "apps.accounts.admin_site.AuditedAdminSite"

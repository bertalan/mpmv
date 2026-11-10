from django.apps import AppConfig
from django.core import checks


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    # Audit policy (SEC.4, docs/dev/audit-trail.md): users, credentials and permissions.
    audited_models = (
        "user",
        "passkeydevice",
        "recoverycodedevice",
        "recoverycode",
        "auth.group",  # managed in the user admin's audited GroupAdmin
        "otp_totp.totpdevice",  # written only by the audited MFA services
        "actionlinkuse",  # CORE.15: each spent link writes `accounts.action_link.signed_in`
        # CORE.15: an emailed link writes `accounts.action_link.sent` with its generation
        "actionlinkissue",
    )

    def ready(self) -> None:
        from django.contrib.auth.signals import user_logged_in, user_login_failed

        from .checks import check_captcha_settings, check_mfa_middleware
        from .signals import count_failed_login, reset_after_login, seed_language_on_login

        user_login_failed.connect(count_failed_login, dispatch_uid="accounts.count_failed_login")
        user_logged_in.connect(reset_after_login, dispatch_uid="accounts.reset_after_login")
        user_logged_in.connect(seed_language_on_login, dispatch_uid="accounts.seed_language")
        checks.register(check_captcha_settings, checks.Tags.security)
        checks.register(check_mfa_middleware, checks.Tags.security)

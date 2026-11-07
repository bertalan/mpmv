from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        from django.contrib.auth.signals import user_logged_in, user_login_failed
        from django.core import checks

        from .checks import check_captcha_settings
        from .signals import count_failed_login, reset_after_login

        user_login_failed.connect(count_failed_login, dispatch_uid="accounts.count_failed_login")
        user_logged_in.connect(reset_after_login, dispatch_uid="accounts.reset_after_login")
        checks.register(check_captcha_settings, checks.Tags.security)

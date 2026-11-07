"""Accounts: one user model for customers, vendor members and staff (CORE.1)."""

from typing import Any, ClassVar

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django_otp.models import Device, ThrottlingMixin

from apps.core.fields import LanguageField
from apps.core.models import BaseModel

from . import engine


def normalize_email_address(email: str) -> str:
    """Emails are compared case-insensitively; we store them trimmed and lower-cased."""
    return email.strip().lower()


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra: Any) -> "User":
        if not email or not email.strip():
            raise ValueError("Users must have an email address")
        user = self.model(email=normalize_email_address(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra["is_staff"] is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra["is_superuser"] is not True:
            raise ValueError("Superuser must have is_superuser=True")
        return self._create(email, password, **extra)

    def get_by_natural_key(self, username: str | None) -> "User":
        return self.get(email=normalize_email_address(username or ""))


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(max_length=254, unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    preferred_language = LanguageField(default=settings.LANGUAGE_CODE)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can log into the staff admin.")
    date_joined = models.DateTimeField(default=timezone.now)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self.email = normalize_email_address(self.email)


# --- Second factors (CORE.4). Each is a django-otp Device, so a verified session remembers the
# device that verified it (`otp_device_id`) and loses it when the device is deleted.


class PasskeyDevice(BaseModel, Device):  # type: ignore[misc]  # django-otp is untyped
    """A WebAuthn credential (passkey or security key) used as a second factor."""

    credential_id = models.CharField(max_length=1400, unique=True, help_text="base64url")
    public_key = models.BinaryField(help_text="COSE-encoded public key.")
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    aaguid = models.CharField("AAGUID", max_length=36, blank=True)
    backed_up = models.BooleanField(default=False, help_text="Synced passkey (multi-device).")
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "passkey"

    def verify_token(self, token: object) -> bool:
        return False  # passkeys answer a WebAuthn challenge, never a typed token


class RecoveryCodeDevice(BaseModel, ThrottlingMixin, Device):  # type: ignore[misc]
    """A user's set of single-use recovery codes; failed attempts are throttled."""

    class Meta:
        verbose_name = "recovery code set"

    def get_throttle_factor(self) -> int:
        return int(getattr(settings, "MFA_RECOVERY_THROTTLE_FACTOR", 1))

    def verify_token(self, token: object) -> bool:
        """Consume a matching unused code. Callers lock the row (`for_verify`) for concurrency."""
        allowed, _ = self.verify_is_allowed()
        if not allowed or not isinstance(token, str):
            return False
        used = self.codes.filter(
            code_hash=engine.hash_recovery_code(token), used_at__isnull=True
        ).update(used_at=timezone.now())
        if used:
            self.throttle_reset()
        else:
            self.throttle_increment()
        return bool(used)


class RecoveryCode(BaseModel):
    """One recovery code, stored as the SHA-256 of its normalized form; used at most once."""

    device = models.ForeignKey(RecoveryCodeDevice, on_delete=models.CASCADE, related_name="codes")
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "code_hash"], name="accounts_recoverycode_unique_per_device"
            ),
        ]

    def __str__(self) -> str:
        return f"recovery code {'used' if self.used_at else 'unused'} ({self.device_id})"

"""Accounts: one user model for customers, vendor members and staff (CORE.1)."""

from typing import Any, ClassVar

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.models import BaseModel


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
    preferred_language = models.CharField(
        max_length=8, choices=settings.LANGUAGES, default=settings.LANGUAGE_CODE
    )
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

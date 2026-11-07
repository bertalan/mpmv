"""factory_boy factories for accounts (ARC.12). Synthetic values only."""

import secrets
from collections.abc import Iterable
from typing import Any

import factory
from django.contrib.auth.models import Permission
from factory.django import DjangoModelFactory

from apps.accounts.models import User


class UserFactory(DjangoModelFactory[User]):
    """A customer by default; `staff=True` or `superuser=True` for the admin.

    The password is hashed with `set_password`. Pass `password=` (generated with
    `secrets.token_urlsafe()`) when the test logs in; otherwise a random one nobody knows is set.
    `permissions=["app_label.codename", ...]` grants model permissions.
    """

    class Meta:
        model = User
        skip_postgeneration_save = True

    class Params:
        staff = factory.Trait(is_staff=True)
        superuser = factory.Trait(is_staff=True, is_superuser=True)

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Faker("name")
    preferred_language = "en"

    @factory.post_generation
    def password(obj: User, create: bool, extracted: str | None, **kwargs: Any) -> None:
        obj.set_password(extracted or secrets.token_urlsafe(16))
        if create:
            obj.save(update_fields=["password"])

    @factory.post_generation
    def permissions(
        obj: User, create: bool, extracted: Iterable[str] | None, **kwargs: Any
    ) -> None:
        if not create or not extracted:
            return
        for label in extracted:
            app_label, codename = label.split(".", 1)
            obj.user_permissions.add(
                Permission.objects.get(content_type__app_label=app_label, codename=codename)
            )

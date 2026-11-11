"""factory_boy factories for accounts (ARC.12). Synthetic values only."""

import secrets
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

import factory
from django.contrib.auth.models import Permission
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from factory.django import DjangoModelFactory

from apps.accounts import engine
from apps.accounts.models import (
    ActionLinkIssue,
    ActionLinkUse,
    ElevationGrant,
    PasskeyDevice,
    RecoveryCode,
    RecoveryCodeDevice,
    User,
)


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

    @factory.post_generation
    def platform_staff(obj: User, create: bool, extracted: bool | None, **kwargs: Any) -> None:
        """`platform_staff=True`: admin login plus `vendors.view_all_vendors` (spec 02, Q-34).

        Plain `staff=True` is Wagtail/Django admin login only and sees no vendor's data.
        """
        if not create or not extracted:
            return
        obj.is_staff = True
        obj.save(update_fields=["is_staff"])
        obj.user_permissions.add(Permission.objects.get(codename="view_all_vendors"))


class ElevationGrantFactory(DjangoModelFactory[ElevationGrant]):
    """A live "Do it once" grant for a given user (session hash and digest are random)."""

    class Meta:
        model = ElevationGrant

    user = factory.SubFactory(UserFactory)
    session_hash = factory.LazyFunction(lambda: secrets.token_hex(32))
    action = "audit_in_clear"
    digest = factory.LazyFunction(lambda: secrets.token_hex(32))
    expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(seconds=120))


class TOTPDeviceFactory(DjangoModelFactory[TOTPDevice]):
    """A confirmed authenticator-app device (django-otp). The key is random, from the model."""

    class Meta:
        model = TOTPDevice

    user = factory.SubFactory(UserFactory)
    name = "Authenticator app"
    confirmed = True


class PasskeyDeviceFactory(DjangoModelFactory[PasskeyDevice]):
    """A confirmed passkey. The public key is random bytes: use FakeAuthenticator to sign in."""

    class Meta:
        model = PasskeyDevice

    user = factory.SubFactory(UserFactory)
    name = "Passkey"
    confirmed = True
    credential_id = factory.LazyFunction(lambda: secrets.token_urlsafe(32))
    public_key = factory.LazyFunction(lambda: secrets.token_bytes(77))
    sign_count = 0
    transports = factory.LazyFunction(lambda: ["internal"])


class RecoveryCodeDeviceFactory(DjangoModelFactory[RecoveryCodeDevice]):
    """A user's set of recovery codes; add codes with RecoveryCodeFactory."""

    class Meta:
        model = RecoveryCodeDevice

    user = factory.SubFactory(UserFactory)
    name = "Recovery codes"
    confirmed = True


class RecoveryCodeFactory(DjangoModelFactory[RecoveryCode]):
    """One unused recovery code. Pass `code=` to know the plain code; only its hash is stored."""

    class Meta:
        model = RecoveryCode
        exclude = ("code",)

    device = factory.SubFactory(RecoveryCodeDeviceFactory)
    code = factory.LazyFunction(lambda: engine.generate_recovery_codes(1)[0])
    code_hash = factory.LazyAttribute(lambda o: engine.hash_recovery_code(o.code))


class ActionLinkUseFactory(DjangoModelFactory[ActionLinkUse]):
    """A spent secure action link (CORE.15): the digest of a random nonce."""

    class Meta:
        model = ActionLinkUse

    nonce_digest = factory.LazyFunction(
        lambda: engine.action_nonce_digest(secrets.token_urlsafe(16))
    )
    purpose = "tests.approve"
    object_key = factory.Sequence(lambda n: f"doc-{n}")
    user = factory.SubFactory(UserFactory)


class ActionLinkIssueFactory(DjangoModelFactory[ActionLinkIssue]):
    """The issue count of secure action links for one purpose, object and recipient (CORE.15)."""

    class Meta:
        model = ActionLinkIssue

    purpose = "tests.approve"
    object_key = factory.Sequence(lambda n: f"doc-{n}")
    user = factory.SubFactory(UserFactory)
    generation = 1

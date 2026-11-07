"""CMS.1: the primary-language HomePage replaces Wagtail's welcome page as the site root.

Wagtail's initial data creates a placeholder page ("Welcome to your new Wagtail site!") at the
top of the tree and a default Site on it. This puts an EN `HomePage` in its place, so `/`
answers right after `migrate`. Translations and sample pages are not migrations: see
`manage.py seed_cms`.
"""

import uuid
from typing import Any

from django.conf import settings
from django.db import migrations

HOME_PATH = "00010001"  # the first child of the tree root


def create_homepage(apps: Any, schema_editor: Any) -> None:
    ContentType = apps.get_model("contenttypes.ContentType")
    Locale = apps.get_model("wagtailcore.Locale")
    Page = apps.get_model("wagtailcore.Page")
    Site = apps.get_model("wagtailcore.Site")
    HomePage = apps.get_model("cms.HomePage")

    if HomePage.objects.exists():
        return
    welcome = Page.objects.filter(
        path=HOME_PATH, content_type__app_label="wagtailcore", content_type__model="page"
    ).first()
    if welcome is None and Page.objects.filter(path=HOME_PATH).exists():
        return  # the tree was already customised: leave it to the editors
    locale = welcome.locale if welcome is not None else None
    if welcome is not None:
        welcome.delete()  # its Site goes with it
    if locale is None:
        locale, _ = Locale.objects.get_or_create(language_code=settings.LANGUAGE_CODE)
    if not Page.objects.filter(depth=1).exists():
        return  # no tree root: Wagtail's initial data did not run

    content_type, _ = ContentType.objects.get_or_create(app_label="cms", model="homepage")
    home = HomePage.objects.create(
        title="Home",
        draft_title="Home",
        slug="home",
        content_type=content_type,
        path=HOME_PATH,
        depth=2,
        numchild=0,
        url_path="/home/",
        locale=locale,
        translation_key=uuid.uuid4(),
        live=True,
    )
    Page.objects.filter(depth=1).update(numchild=Page.objects.filter(depth=2).count())
    Site.objects.create(
        hostname="localhost",
        port=80,
        root_page_id=home.pk,
        is_default_site=True,
        site_name=settings.WAGTAIL_SITE_NAME,
    )


def remove_homepage(apps: Any, schema_editor: Any) -> None:
    HomePage = apps.get_model("cms.HomePage")
    Page = apps.get_model("wagtailcore.Page")
    # Child pages and the Site on the home page are deleted with it (CASCADE).
    Page.objects.filter(pk__in=HomePage.objects.values("pk")).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0001_initial"),
        ("wagtailcore", "0098_apitoken"),
    ]

    operations = [
        migrations.RunPython(create_homepage, remove_homepage),
    ]

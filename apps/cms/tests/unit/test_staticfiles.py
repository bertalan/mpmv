"""CMS.1: the image build collects Wagtail's static files with hashed names (ARC.5).

Wagtail's admin CSS points at an image of `wagtail.images`, which this project does not install;
the manifest storage would stop `collectstatic` on that missing file and fail the image build.
"""

from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.cms.staticfiles import UNINSTALLED_APP_FILES, StaticFilesStorage

STORAGE = "apps.cms.staticfiles.StaticFilesStorage"


def test_collectstatic_hashes_the_wagtail_admin(tmp_path: Path) -> None:
    storages = {"staticfiles": {"BACKEND": STORAGE}}
    with override_settings(STATIC_ROOT=tmp_path, STORAGES=storages):
        call_command("collectstatic", "--noinput", verbosity=0)
    assert (tmp_path / "staticfiles.json").is_file()
    assert list(tmp_path.glob("wagtailadmin/css/core.*.css"))


def test_only_the_listed_files_of_uninstalled_apps_may_be_missing(tmp_path: Path) -> None:
    storage = StaticFilesStorage(location=tmp_path)
    for name in UNINSTALLED_APP_FILES:
        assert storage.hashed_name(name) == name
    with pytest.raises(ValueError, match="could not be found"):
        storage.hashed_name("wagtailadmin/images/missing.gif")

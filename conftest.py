import os

import django

pytest_plugins = ["tests.conftest_git", "tests.conftest_lock"]


def pytest_configure():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datum.settings")
    django.setup()

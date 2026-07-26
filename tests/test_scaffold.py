import importlib


def test_settings_module_imports():
    settings = importlib.import_module("datum.settings")
    assert settings.INSTALLED_APPS  # apps registered


def test_kernel_apps_present():
    from datum import settings

    for app in (
        "datum.kinds",
        "datum.graph",
        "datum.intent",
        "datum.discovery",
        "datum.reconcile",
        "datum.workflow",
        "datum.api",
    ):
        assert app in settings.INSTALLED_APPS

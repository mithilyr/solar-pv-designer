import pytest

from solarpv.bom import load_rules
from solarpv.library import load_library


@pytest.fixture(autouse=True)
def isolated_user_dir(tmp_path, monkeypatch):
    """Never read or write the developer's own library or rules."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture
def library():
    return load_library(use_user_copy=False)


@pytest.fixture
def rules():
    return load_rules(use_user_copy=False)


@pytest.fixture
def panel(library):
    return library.panel("JA Solar JAM72D40-595/LB")

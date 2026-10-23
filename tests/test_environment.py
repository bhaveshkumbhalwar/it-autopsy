"""Environment sanity checks introduced in Phase 1."""

from __future__ import annotations

import sys


def test_python_version_is_supported():
    # Tested on 3.14.6. The project uses modern typing syntax, so require 3.10+.
    assert sys.version_info >= (3, 10), sys.version


def test_core_imports_work():
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import sklearn  # noqa: F401
    import streamlit  # noqa: F401


def test_project_package_is_importable():
    import src

    assert src.__version__


def test_config_paths_are_absolute():
    from src import config

    for path in (config.DATA_DIR, config.MODELS_DIR, config.REPORTS_DIR):
        assert path.is_absolute()


def test_db_path_can_be_overridden(monkeypatch, tmp_path):
    from src import config

    target = tmp_path / "custom.db"
    monkeypatch.setenv("HDA_DB_PATH", str(target))
    assert config.get_db_path() == target

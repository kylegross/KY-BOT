"""Isolate test temporary files from shared Windows system-temp permissions."""

import tempfile

import pytest

_TEMP_DIRECTORY = pytest.StashKey[tempfile.TemporaryDirectory]()


def pytest_configure(config: pytest.Config) -> None:
    # Honour an explicitly supplied --basetemp (for example, in CI).
    if config.option.basetemp is not None:
        return
    cache = config.rootpath / ".pytest_cache"
    cache.mkdir(exist_ok=True)
    directory = tempfile.TemporaryDirectory(prefix="ky-bot-tests-", dir=cache)
    config.stash[_TEMP_DIRECTORY] = directory
    config.option.basetemp = directory.name


def pytest_unconfigure(config: pytest.Config) -> None:
    directory = config.stash.get(_TEMP_DIRECTORY, None)
    if directory is not None:
        directory.cleanup()

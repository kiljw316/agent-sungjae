"""Root conftest: shared fixtures for server and client tests."""

from __future__ import annotations

import pytest

from server.tests.support import (  # noqa: F401  (pytest fixtures)
    client,
    clock,
    db_url,
    make_container,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-network", action="store_true", help="run tests that hit the real internet")
    parser.addoption("--run-voice", action="store_true", help="run tests that need whisper model + macOS say")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, opt in (("network", "--run-network"), ("voice", "--run-voice")):
        if config.getoption(opt):
            continue
        skip = pytest.mark.skip(reason=f"needs {opt}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)

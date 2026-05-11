"""Shared pytest fixtures.

The ``pytest-homeassistant-custom-component`` plugin (used by
``tests/ha/``) auto-loads as a pytest plugin and globally disables
sockets via ``pytest-socket``. The library unit tests
(``tests/test_*.py``) mock HTTP via ``aioresponses`` and don't need
real sockets, but the e2e suite (``tests/e2e``) hits the real eVisitor
server. The autouse fixture below re-enables sockets for everything
outside ``tests/ha/``.
"""

from __future__ import annotations

import os
import re

import pytest

from pyevisitor import EVisitorConfig, Environment


@pytest.fixture(autouse=True)
def _enable_sockets_outside_ha(request):
    """Enable sockets for non-HA tests.

    pHACC's autouse fixture disables sockets globally; for tests outside
    ``tests/ha/`` we re-enable. We don't re-disable on teardown because
    pHACC's per-test setup will do so before the next HA test runs anyway,
    and re-disabling here breaks subsequent non-HA tests in the same module.
    """
    in_ha_tests = "/tests/ha/" in str(request.fspath).replace("\\", "/")
    if in_ha_tests:
        return
    try:
        import pytest_socket

        pytest_socket.enable_socket()
    except ImportError:
        pass


def url_re(pattern: str) -> re.Pattern[str]:
    """Build a regex URL matcher for use with aioresponses.

    aioresponses 0.7.8 + aiohttp 3.13 does not match registered URLs
    against requests that include a query string (and double-encodes
    when the value contains ``[``/``{``). Wrap test URLs with this so
    matchers ignore query parameters cleanly.
    """
    return re.compile("^" + re.escape(pattern) + r"(\?.*)?$")


@pytest.fixture
def url_re_helper():
    """Expose :func:`url_re` as a pytest fixture if desired."""
    return url_re


@pytest.fixture
def test_config() -> EVisitorConfig:
    """Synthetic test-environment config used by the unit tests."""
    return EVisitorConfig(
        username="user",
        password="pass",
        environment=Environment.TEST,
        api_key="testkey",
    )


@pytest.fixture
def prod_config() -> EVisitorConfig:
    return EVisitorConfig(
        username="user",
        password="pass",
        environment=Environment.PRODUCTION,
    )


@pytest.fixture
def e2e_config() -> EVisitorConfig:
    """Real e-Visitor config built from environment variables.

    Skips the test when ``EVISITOR_E2E`` is not set or credentials are
    missing. Will run against whichever environment ``EVISITOR_ENVIRONMENT``
    selects -- including production. The e2e suite is read-only.
    """
    if os.environ.get("EVISITOR_E2E", "0") != "1":
        pytest.skip("EVISITOR_E2E != 1; live e2e tests disabled")
    if not (os.environ.get("EVISITOR_USERNAME") and os.environ.get("EVISITOR_PASSWORD")):
        pytest.skip("EVISITOR_USERNAME/PASSWORD not set")

    return EVisitorConfig.from_env()

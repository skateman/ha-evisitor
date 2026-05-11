from __future__ import annotations

import pytest

from pyevisitor import EVisitorConfig, Environment


def test_environment_parse_aliases() -> None:
    assert Environment.parse("prod") is Environment.PRODUCTION
    assert Environment.parse("Production") is Environment.PRODUCTION
    assert Environment.parse("test") is Environment.TEST
    assert Environment.parse("TESTING") is Environment.TEST


def test_environment_parse_invalid() -> None:
    with pytest.raises(ValueError):
        Environment.parse("nope")


def test_test_env_warns_when_api_key_missing(caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="pyevisitor.config"):
        cfg = EVisitorConfig(
            username="u", password="p", environment=Environment.TEST
        )
    assert cfg.api_key is None
    assert any("api_key" in rec.message for rec in caplog.records)


def test_prod_env_works_without_api_key() -> None:
    cfg = EVisitorConfig(
        username="u", password="p", environment=Environment.PRODUCTION
    )
    assert cfg.api_key is None
    assert cfg.api_root == "https://www.evisitor.hr/eVisitorRhetos_API"
    assert cfg.rest_root.endswith("/Rest/")
    assert "AspNetFormsAuth/Authentication/" in cfg.auth_root


def test_test_env_uses_testapi_root() -> None:
    cfg = EVisitorConfig(
        username="u",
        password="p",
        environment=Environment.TEST,
        api_key="k",
    )
    assert cfg.api_root == "https://www.evisitor.hr/testApi"


def test_from_env_reads_prefixed_vars() -> None:
    env = {
        "EVISITOR_USERNAME": "alice",
        "EVISITOR_PASSWORD": "secret",
        "EVISITOR_ENVIRONMENT": "test",
        "EVISITOR_API_KEY": "k",
    }
    cfg = EVisitorConfig.from_env(env=env)
    assert cfg.username == "alice"
    assert cfg.environment is Environment.TEST
    assert cfg.api_key == "k"

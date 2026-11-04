"""`kirocrew doctor` reports a credentials file it may not read instead of crashing.

Inside an agent sandbox the credentials read can raise PermissionError. The
Configuration section prints the file as unreadable, records one issue, and
returns a binding with the environment's credentials only, so the rest of the
report still runs.
"""

from __future__ import annotations

from types import SimpleNamespace

from kiro_crew.config import loader_credentials
from kiro_crew.config.loader_credentials import CREDENTIAL_KEYS
from kiro_crew.doctor_checks import services


def _cfg(url: str):
    def _denied():
        raise PermissionError(1, "Operation not permitted")

    return SimpleNamespace(
        agent=SimpleNamespace(provider="kiro", model="auto", approval_mode="auto"),
        dashboard=SimpleNamespace(url=url),
        load_credentials=_denied,
    )


def test_unreadable_credentials_are_reported_not_raised(monkeypatch, capsys):
    monkeypatch.delenv("KIROCREW_PORT", raising=False)
    for key in CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    issues: list[str] = []

    binding = services._doctor_configuration(_cfg("http://localhost:8080"), issues)

    out = capsys.readouterr().out
    assert "credentials: ⚠️  unreadable here (Operation not permitted)" in out
    assert issues == ["credentials file unreadable (permission denied)"]
    assert binding.creds == {}
    assert binding.has_slack is False


def test_unreadable_credentials_keep_the_environment_tokens(monkeypatch, capsys):
    monkeypatch.delenv("KIROCREW_PORT", raising=False)
    monkeypatch.setenv("SLACK_APP_TOKEN", "app-token-value")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "bot-token-value")

    binding = services._doctor_configuration(_cfg("http://localhost:8080"), [])

    assert binding.creds["SLACK_APP_TOKEN"] == "app-token-value"
    assert binding.creds["SLACK_BOT_TOKEN"] == "bot-token-value"
    assert binding.has_slack is True


def test_unreadable_credentials_resolve_environment_secret_references(monkeypatch, capsys):
    monkeypatch.delenv("KIROCREW_PORT", raising=False)
    monkeypatch.setenv("SLACK_APP_TOKEN", "secret://missing-name")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "bot-token-value")
    seen: list[dict[str, str]] = []

    def _resolve(values, **_kwargs):
        seen.append(dict(values))
        return {k: v for k, v in values.items() if not v.startswith("secret://")}

    monkeypatch.setattr(loader_credentials, "resolve_env_credentials", _resolve)
    binding = services._doctor_configuration(_cfg("http://localhost:8080"), [])

    assert seen and seen[0]["SLACK_APP_TOKEN"] == "secret://missing-name"
    assert "SLACK_APP_TOKEN" not in binding.creds
    assert binding.has_slack is False


def test_unreadable_credentials_without_strerror_still_report(monkeypatch, capsys):
    monkeypatch.delenv("KIROCREW_PORT", raising=False)

    def _denied():
        raise PermissionError()

    cfg = _cfg("http://localhost:8080")
    cfg.load_credentials = _denied
    services._doctor_configuration(cfg, [])

    assert "credentials: ⚠️  unreadable here (permission denied)" in capsys.readouterr().out

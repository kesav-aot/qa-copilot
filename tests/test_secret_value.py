import json

import pytest

from qa_copilot.secrets.base import SecretAccessViolation, SecretValue
from qa_copilot.secrets.env import EnvSecretProvider, ref_to_env_var


def test_secret_never_renders_its_value():
    s = SecretValue(alias="secret://demo/admin/password", _value="Adm1n-Demo-Pass!")
    assert "Adm1n-Demo-Pass!" not in str(s)
    assert "Adm1n-Demo-Pass!" not in repr(s)
    assert "Adm1n-Demo-Pass!" not in f"{s}"
    assert "Adm1n-Demo-Pass!" not in f"{s!r}"
    with pytest.raises(TypeError):
        json.dumps(s)


def test_reveal_is_refused_outside_the_executor():
    s = SecretValue(alias="secret://x", _value="value-goes-here")
    with pytest.raises(SecretAccessViolation):
        s.reveal()


def test_resolving_a_secret_registers_it_for_redaction():
    from qa_copilot.sanitize import sanitizer

    SecretValue(alias="secret://x", _value="registered-on-construction")
    assert "registered-on-construction" in sanitizer.registry().known_values()


def test_env_reference_mapping():
    assert ref_to_env_var("secret://demo/admin/password") == "QA_SECRET__DEMO__ADMIN__PASSWORD"


def test_env_provider_reports_missing_refs_clearly(monkeypatch):
    monkeypatch.delenv("QA_SECRET__NOPE__NOPE", raising=False)
    provider = EnvSecretProvider()
    assert not provider.has("secret://nope/nope")
    with pytest.raises(KeyError):
        provider.get("secret://nope/nope")

import pytest

from qa_copilot.identity.broker import IdentityError


def test_public_view_never_exposes_secret_references(copilot):
    blob = repr(copilot.list_identities())
    assert "secret://" not in blob
    assert "password" not in blob.lower()


def test_capabilities_are_discoverable(copilot):
    caps = copilot.list_capabilities()
    assert "manage_users" in caps and "create_order" in caps


def test_capability_selection_prefers_least_privilege(copilot):
    chosen = copilot.broker.select("browse", "demo")
    assert chosen.alias == "STANDARD_USER"


def test_capability_selection_finds_the_only_holder(copilot):
    assert copilot.broker.select("manage_settings", "demo").alias == "ADMIN_USER"


def test_unknown_capability_lists_what_is_available(copilot):
    with pytest.raises(IdentityError, match="available capabilities"):
        copilot.broker.select("launch_missiles", "demo")


def test_credentials_are_wrapped_not_plain(copilot):
    identity = copilot.config.identity("ADMIN_USER")
    creds = copilot.broker.credentials(identity)
    assert "Adm1n-Demo-Pass!" not in repr(creds)
    assert str(creds.password) == "secret://demo/admin/password"

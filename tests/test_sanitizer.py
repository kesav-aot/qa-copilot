from qa_copilot.sanitize import sanitizer


def test_registered_value_is_redacted_anywhere_it_appears():
    sanitizer.registry().register("Adm1n-Demo-Pass!")
    out = sanitizer.scrub({"log": ["typed Adm1n-Demo-Pass! into #password"]})
    assert "Adm1n-Demo-Pass!" not in repr(out)
    assert "[REDACTED]" in out["log"][0]


def test_registry_ignores_values_too_short_to_redact_safely():
    sanitizer.registry().register("ab")
    assert sanitizer.scrub("ab cd") == "ab cd"


def test_jwt_and_bearer_are_redacted_without_prior_knowledge():
    text = (
        "Authorization: Bearer "
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    out = sanitizer.scrub_text(text)
    assert "eyJ" not in out
    assert "[REDACTED]" in out


def test_password_key_value_pairs_are_redacted():
    assert "hunter2" not in sanitizer.scrub_text('{"password": "hunter2"}')


def test_connection_strings_are_redacted():
    out = sanitizer.scrub_text("postgres://qa:s3cr3tpw@db.internal:5432/app")
    assert "s3cr3tpw" not in out


def test_scrub_walks_nested_structures_and_keys():
    sanitizer.registry().register("topsecretvalue")
    out = sanitizer.scrub({"a": [{"b": ("topsecretvalue",)}], "topsecretvalue": 1})
    assert "topsecretvalue" not in repr(out)


def test_contains_secret_is_the_egress_tripwire():
    sanitizer.registry().register("leakedpassword")
    assert sanitizer.contains_secret({"x": "leakedpassword"})
    assert not sanitizer.contains_secret({"x": "fine"})

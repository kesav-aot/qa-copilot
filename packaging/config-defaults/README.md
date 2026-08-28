# What a fresh install starts with

The bundle ships these as the starting workspace, **not** the `config/` at the
repository root. That directory is a working configuration: whoever is
developing QA Copilot points it at their own applications, and those
environments, identity aliases and host names must not be shipped to everyone
who installs the extension.

Keep this demo-only. `tests/test_packaging.py` fails if anything else appears.

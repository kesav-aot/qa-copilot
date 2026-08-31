"""What the distributed bundle contains.

The regression: the build copied the repository's live `config/` as the
extension's starting workspace. Whoever develops QA Copilot points that at their
own applications, so the bundle began shipping their environments, identity
aliases and internal host names to everyone who installed it — and a fresh
install then failed to provision, because the environment it was creating
already existed.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> zipfile.ZipFile:
    subprocess.run(
        [sys.executable, "scripts/build_mcpb.py"], cwd=ROOT, check=True, capture_output=True
    )
    built = sorted((ROOT / "dist").glob("qa-copilot-*.mcpb"))
    assert built, "the build produced no bundle"
    return zipfile.ZipFile(built[-1])


def _yaml(bundle: zipfile.ZipFile, name: str) -> dict:
    return yaml.safe_load(bundle.read(f"config-defaults/{name}").decode())


def test_a_fresh_install_knows_about_no_applications(bundle):
    """Anything shipped here is asked about by everyone who installs it."""
    assert _yaml(bundle, "environments.yaml")["environments"] == {}


def test_a_fresh_install_knows_about_no_accounts(bundle):
    assert _yaml(bundle, "identities.yaml")["identities"] == {}


def test_the_shipped_allow_list_is_inert_but_not_empty(bundle):
    """An empty allow-list disables the check: PolicyEngine reads it as
    'no restriction'. So the default must match nothing without being []."""
    policy = _yaml(bundle, "settings.yaml")["policy"]
    assert policy["allowed_environments"], "an empty list would allow every environment"
    environments = _yaml(bundle, "environments.yaml")["environments"]
    assert not set(policy["allowed_environments"]) & set(environments)
    assert "production" in policy["blocked_environments"]


def test_the_bundle_carries_no_secrets_file(bundle):
    names = bundle.namelist()
    assert not [n for n in names if Path(n).name in (".env", "secrets.enc.yaml")]


def test_the_bundle_carries_both_launchers_and_the_manifest(bundle):
    names = set(bundle.namelist())
    assert {"manifest.json", "bin/qa-copilot-launch", "bin/qa-copilot-launch.ps1"} <= names


def test_the_launcher_stays_executable_through_the_zip(bundle):
    mode = bundle.getinfo("bin/qa-copilot-launch").external_attr >> 16
    assert mode & 0o111, "a launcher without the executable bit fails at install time"


def test_the_release_check_runs_with_only_the_standard_library():
    """The first release failed here: the check imported PyYAML, which a bare
    runner does not have. The build itself is stdlib-only, and the gate in
    front of publishing has to be too, or it is a dependency on CI luck."""
    source = (ROOT / "scripts" / "check_mcpb.py").read_text()
    for banned in ("import yaml", "import requests", "from yaml"):
        assert banned not in source, f"check_mcpb.py must not {banned}"


def test_the_release_check_rejects_a_bundle_carrying_configuration():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from check_mcpb import _mapping_is_empty

    assert _mapping_is_empty(b"environments: {}\n", "environments")
    assert _mapping_is_empty(b"# a comment\nidentities: {}\n", "identities")
    assert not _mapping_is_empty(
        b"environments:\n  demo:\n    base_url: http://x\n", "environments"
    )

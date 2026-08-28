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


def test_the_bundle_ships_only_the_demo_environment(bundle):
    assert list(_yaml(bundle, "environments.yaml")["environments"]) == ["demo"]


def test_the_bundle_ships_only_the_demo_accounts(bundle):
    assert sorted(_yaml(bundle, "identities.yaml")["identities"]) == [
        "ADMIN_USER",
        "STANDARD_USER",
    ]


def test_the_bundle_allows_only_the_demo_environment(bundle):
    policy = _yaml(bundle, "settings.yaml")["policy"]
    assert policy["allowed_environments"] == ["demo"]
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

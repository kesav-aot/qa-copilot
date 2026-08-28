"""Build the Claude Desktop extension bundle.

A .mcpb is a zip with manifest.json at its root. We assemble a staging tree so
the archive holds exactly what the launcher expects and nothing else — in
particular no .env, no virtualenv, and no previous run's artifacts.

    python scripts/build_mcpb.py            # -> dist/qa-copilot-<version>.mcpb
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging" / "mcpb"
DIST = ROOT / "dist"

# Copied into the bundle as the starting workspace. The demo environment means a
# freshly installed extension has something that works before it is configured.
CONFIG_DEFAULTS = ["environments.yaml", "identities.yaml", "settings.yaml", "suites.yaml"]

# Anything matching these never belongs in a distributed artifact.
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".git", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=lambda _dir, names: [
            n for n in names if n in EXCLUDE_DIRS or Path(n).suffix in EXCLUDE_SUFFIXES
        ],
    )


def build() -> Path:
    manifest = json.loads((PKG / "manifest.json").read_text())
    version = manifest["version"]

    stage = DIST / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # 1. the manifest and the launcher
    shutil.copy2(PKG / "manifest.json", stage / "manifest.json")
    # The launchers are shared with the Claude Code plugin, so they live outside
    # the bundle directory and are copied into the bin/ the manifest expects.
    _copy_tree(ROOT / "packaging" / "launcher", stage / "bin")
    _copy_tree(PKG / "bootstrap", stage / "bootstrap")
    (stage / "bin" / "qa-copilot-launch").chmod(0o755)

    # 2. the installable package, source only — uv builds it on the user's machine
    src = stage / "src"
    src.mkdir()
    _copy_tree(ROOT / "qa_copilot", src / "qa_copilot")
    shutil.copy2(ROOT / "pyproject.toml", src / "pyproject.toml")
    for optional in ("README.md",):
        if (ROOT / optional).is_file():
            shutil.copy2(ROOT / optional, src / optional)

    # 3. the starting workspace, and the docs a QA engineer actually needs
    # From packaging/, never from config/. That directory is a working
    # configuration — whoever develops QA Copilot points it at their own
    # applications, and those environments, aliases and host names must not be
    # shipped to everyone who installs the extension.
    defaults = stage / "config-defaults"
    defaults.mkdir()
    for name in CONFIG_DEFAULTS:
        shutil.copy2(ROOT / "packaging" / "config-defaults" / name, defaults / name)
    docs = stage / "docs"
    docs.mkdir()
    for name in ("WRITING-TESTS.md", "CONNECT.md"):
        if (ROOT / "docs" / name).is_file():
            shutil.copy2(ROOT / "docs" / name, docs / name)

    # 4. the stamp the launcher compares against, to know when to reinstall
    (stage / "VERSION").write_text(version)

    out = DIST / f"qa-copilot-{version}.mcpb"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(str(path.relative_to(stage)))
                info.compress_type = zipfile.ZIP_DEFLATED
                # Preserve the executable bit; a launcher that arrives without
                # it fails at install time with a bare "permission denied".
                mode = path.stat().st_mode & 0o777
                info.external_attr = (mode | 0o100000) << 16
                zf.writestr(info, path.read_bytes())

    shutil.rmtree(stage)
    return out


if __name__ == "__main__":
    bundle = build()
    size = bundle.stat().st_size / 1024
    print(f"built {bundle.relative_to(ROOT)}  ({size:.0f} KB)")
    print("install: double-click it, or Claude Desktop → Settings → Extensions")
    sys.exit(0)

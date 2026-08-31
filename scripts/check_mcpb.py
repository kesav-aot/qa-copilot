"""Refuse to publish a bundle that is incomplete or carries someone's setup.

Run by the release workflow between building and publishing. Kept as a file
rather than inline in the workflow so it can be run locally, and so a heredoc's
indentation inside YAML cannot change what it does.

    python scripts/check_mcpb.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = {"manifest.json", "bin/qa-copilot-launch", "bin/qa-copilot-launch.ps1"}


def _mapping_is_empty(raw: bytes, key: str) -> bool:
    """True when `key:` maps to nothing.

    Deliberately not using PyYAML: this runs on a bare CI runner, and the
    published artifact should not depend on a package being installed there.
    The shape being checked is a top-level key followed by either `{}` or by
    nothing but comments.
    """
    lines = [
        line.rstrip()
        for line in raw.decode("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith(f"{key}:"))
    except StopIteration:
        return True  # the key is absent, so it holds nothing
    if lines[start].strip() != f"{key}:":
        return lines[start].strip() == f"{key}: {{}}"
    # Any indented line after the key is an entry.
    return not any(line.startswith((" ", "\t")) for line in lines[start + 1 :])


def main() -> int:
    built = sorted((ROOT / "dist").glob("qa-copilot-*.mcpb"))
    if not built:
        print("no bundle in dist/ — run scripts/build_mcpb.py first", file=sys.stderr)
        return 1
    bundle = built[-1]
    zf = zipfile.ZipFile(bundle)

    missing = REQUIRED - set(zf.namelist())
    if missing:
        print(f"bundle is missing {sorted(missing)}", file=sys.stderr)
        return 1

    mode = zf.getinfo("bin/qa-copilot-launch").external_attr >> 16
    if not mode & 0o111:
        print("the launcher lost its executable bit", file=sys.stderr)
        return 1

    for name, key in (("environments", "environments"), ("identities", "identities")):
        raw = zf.read(f"config-defaults/{name}.yaml")
        if not _mapping_is_empty(raw, key):
            print(
                f"bundle ships {name} it should not — a fresh install must know "
                f"about nobody's applications or accounts",
                file=sys.stderr,
            )
            return 1

    print(f"{bundle.name}: {bundle.stat().st_size // 1024} KB, complete and clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

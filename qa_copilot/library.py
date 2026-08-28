"""The plan library: reviewed plans persisted as YAML, plus named suites.

Plans arrive from a model, so every path is derived from a slug this module
computes — a plan name never becomes a filename directly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from qa_copilot.dsl.schema import TestPlan
from qa_copilot.policy.engine import fingerprint


class LibraryError(RuntimeError):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    if not slug:
        raise LibraryError(f"plan name {name!r} produces an empty slug; give it a real name")
    return slug[:80]


class PlanLibrary:
    def __init__(self, root: Path, suites_file: Path | None = None) -> None:
        self.root = Path(root)
        self.suites_file = suites_file

    # --- plans ------------------------------------------------------------
    def path_for(self, name: str) -> Path:
        return self.root / f"{slugify(name)}.yaml"

    def save(self, plan: TestPlan, *, overwrite: bool = True) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(plan.name)
        existed = path.is_file()
        if existed and not overwrite:
            raise LibraryError(
                f"plan {slugify(plan.name)!r} already exists; pass overwrite to replace it"
            )
        body = plan.model_dump(mode="json", exclude_none=True)
        path.write_text(
            yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        return {
            "slug": slugify(plan.name),
            "name": plan.name,
            "path": str(path),
            "replaced": existed,
            "fingerprint": fingerprint(plan),
        }

    def load(self, name: str) -> TestPlan:
        path = self.path_for(name)
        if not path.is_file():
            known = ", ".join(e["slug"] for e in self.list()) or "<library is empty>"
            raise LibraryError(f"no plan named {slugify(name)!r}. Available: {known}")
        return TestPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.yaml")):
            try:
                plan = TestPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001 - a broken file should be visible
                entries.append({"slug": path.stem, "path": str(path), "error": str(exc)[:200]})
                continue
            entries.append(
                {
                    "slug": path.stem,
                    "name": plan.name,
                    "environment": plan.environment,
                    "declared_risk": str(plan.risk),
                    "tags": plan.tags,
                    "steps": len(plan.steps),
                    "fingerprint": fingerprint(plan),
                    "path": str(path),
                }
            )
        return entries

    # --- suites -----------------------------------------------------------
    def suites(self) -> dict[str, list[str]]:
        if not self.suites_file or not self.suites_file.is_file():
            return {}
        data = yaml.safe_load(self.suites_file.read_text(encoding="utf-8")) or {}
        raw = data.get("suites", {}) if isinstance(data, dict) else {}
        return {str(k): [str(v) for v in (vals or [])] for k, vals in raw.items()}

    def resolve_suite(self, name: str) -> list[str]:
        suites = self.suites()
        if name not in suites:
            known = ", ".join(sorted(suites)) or "<none defined>"
            raise LibraryError(f"no suite named {name!r}. Defined suites: {known}")
        missing = [s for s in suites[name] if not self.path_for(s).is_file()]
        if missing:
            raise LibraryError(
                f"suite {name!r} references plans that are not in the library: "
                + ", ".join(missing)
            )
        return suites[name]

"""Skills: the written know-how the assistant loads before it does a job.

A skill is a Markdown file — how to write a test, how to report a run, how one
particular product behaves. They reach the assistant through the MCP server
rather than through a plugin folder, because a Claude Desktop extension is only
an MCP server: a ``skills/`` directory shipped beside it is never read.

Two places are searched:

* **built-in** skills, shipped with QA Copilot;
* **project** skills, in the ``project-skills`` folder of the person's own
  workspace.
  These are how a team teaches the assistant about its own application, and a
  project skill replaces a built-in one of the same name.

Skills are instructions, so they are held to the same rule as everything else
here: no credential is ever written into one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qa_copilot.sanitize import sanitizer

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_MAX_BYTES = 60_000

# The same shapes the plan schema refuses in a `fill` value. A skill is read on
# every job, so a password written into one would be handed to the model daily.
_CREDENTIAL_LINE = re.compile(
    r"(?i)\b(password|passwd|pwd|api[_-]?key|token)\b\s*[:=]\s*[^\s<\[{*]\S{3,}"
)


class SkillError(ValueError):
    pass


@dataclass
class Skill:
    name: str
    description: str
    source: str  # "built-in" | "project"
    path: Path

    def body(self) -> str:
        text = self.path.read_text(encoding="utf-8")
        return _FRONTMATTER.sub("", text, count=1).strip() + "\n"

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "source": self.source}


def builtin_dir() -> Path | None:
    """Where the shipped skills are.

    Inside the package when installed from a bundle (the build copies them
    there), beside it in a checkout or the Claude Code plugin.
    """
    package = Path(__file__).resolve().parent
    for candidate in (package / "builtin_skills", package.parent / "skills"):
        if candidate.is_dir():
            return candidate
    return None


def _read_meta(path: Path) -> dict[str, str]:
    """The frontmatter's simple ``key: value`` lines. Not a YAML parse — a skill
    someone wrote by hand should not fail to load over a stray colon."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = _FRONTMATTER.match(text)
    meta: dict[str, str] = {}
    if match:
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() and not key.startswith((" ", "\t", "#")):
                meta[key.strip().lower()] = value.strip().strip("\"'")
    if "description" not in meta:
        # No frontmatter: the first line of prose is the best description there is.
        body = _FRONTMATTER.sub("", text, count=1)
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                meta["description"] = line[:300]
                break
    return meta


def _scan(root: Path | None, source: str) -> dict[str, Skill]:
    found: dict[str, Skill] = {}
    if root is None or not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            path = entry / "SKILL.md"
            fallback = entry.name
        elif entry.is_file() and entry.suffix.lower() == ".md":
            path = entry
            fallback = entry.stem
        else:
            continue
        meta = _read_meta(path)
        name = re.sub(r"[^a-z0-9-]+", "-", (meta.get("name") or fallback).lower()).strip("-")
        if not name:
            continue
        found[name] = Skill(
            name=name,
            description=meta.get("description", ""),
            source=source,
            path=path,
        )
    return found


class SkillLibrary:
    def __init__(self, project_dir: Path, builtin: Path | None = None) -> None:
        self.project_dir = Path(project_dir)
        self.builtin = builtin if builtin is not None else builtin_dir()

    def _same_folder(self) -> bool:
        """A workspace pointed at the shipped skills must not relabel them as
        the project's own, or let a save overwrite one."""
        try:
            return self.builtin is not None and self.project_dir.resolve() == self.builtin.resolve()
        except OSError:
            return False

    def all(self) -> dict[str, Skill]:
        skills = _scan(self.builtin, "built-in")
        if not self._same_folder():
            skills.update(_scan(self.project_dir, "project"))  # the project's word wins
        return skills

    def list(self) -> list[dict[str, Any]]:
        return [skill.summary() for skill in self.all().values()]

    def get(self, name: str) -> Skill:
        wanted = name.strip().lower()
        skills = self.all()
        if wanted not in skills:
            known = ", ".join(sorted(skills)) or "<none>"
            raise SkillError(f"there is no skill called {name!r}. Available: {known}")
        return skills[wanted]

    def save(self, name: str, description: str, content: str) -> dict[str, Any]:
        """Write a project skill. Never touches a built-in one."""
        slug = name.strip().lower()
        if not _NAME.match(slug):
            raise SkillError(
                "a skill name is 3 to 60 lower-case letters, digits and hyphens, "
                'for example "our-checkout-flow"'
            )
        description = " ".join(description.split())
        if len(description) < 20:
            raise SkillError(
                "say in a sentence when this skill should be used — the description "
                "is how it gets picked"
            )
        content = content.strip()
        if len(content) < 40:
            raise SkillError("the skill has no instructions in it")
        if len(content.encode("utf-8")) > _MAX_BYTES:
            raise SkillError("the skill is too long; split it into two skills")
        combined = f"{description}\n{content}"
        if (
            sanitizer.contains_secret(combined)
            or sanitizer.scrub_text(combined) != combined
            or _CREDENTIAL_LINE.search(combined)
        ):
            raise SkillError(
                "this looks like it contains a credential, and a skill is read on "
                "every job. Name the test account by its alias instead, for example "
                '"sign in as ADMIN_USER"'
            )

        if self._same_folder():
            raise SkillError(
                "skill_dir in settings.yaml points at QA Copilot's own skills; "
                "give project skills a folder of their own"
            )
        folder = self.project_dir / slug
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "SKILL.md"
        replaced = path.is_file()
        path.write_text(
            f"---\nname: {slug}\ndescription: {description}\n---\n\n{content}\n",
            encoding="utf-8",
        )
        return {
            "saved": True,
            "name": slug,
            "path": str(path),
            "replaced_existing": replaced,
            "overrides_built_in": slug in _scan(self.builtin, "built-in"),
        }

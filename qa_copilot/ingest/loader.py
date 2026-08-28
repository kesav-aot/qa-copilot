"""Format detection and directory scanning.

Paths are resolved against a configured root and checked for traversal, because
the path can come from a model.
"""

from __future__ import annotations

from pathlib import Path

from qa_copilot.ingest import parsers
from qa_copilot.ingest.models import IngestResult, ManualTestCase

SUFFIXES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".csv": "csv",
    ".tsv": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".feature": "gherkin",
    ".json": "jira",
    ".txt": "text",
}


class IngestError(RuntimeError):
    pass


def resolve_within(root: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` under ``root``, refusing to escape it."""
    root = root.resolve()
    path = (root / candidate).resolve() if not Path(candidate).is_absolute() else Path(candidate).resolve()
    if root != path and root not in path.parents:
        raise IngestError(f"path {str(candidate)!r} resolves outside the test-case directory")
    return path


def detect_format(path: Path, text: str | None = None) -> str:
    fmt = SUFFIXES.get(path.suffix.lower())
    if fmt is None:
        raise IngestError(
            f"unsupported file type {path.suffix!r}; supported: "
            + ", ".join(sorted(SUFFIXES))
        )
    # A .md holding Gherkin, or a .txt holding CSV, is common enough to sniff.
    if text:
        head = text.lstrip()[:400].lower()
        first_line = head.splitlines()[0] if head.splitlines() else ""
        if (
            fmt in {"markdown", "text"}
            and head.startswith(("feature:", "@"))
            and "scenario" in text.lower()
        ):
            return "gherkin"
        if fmt == "text" and first_line.count(",") >= 2:
            return "csv"
    return fmt


def parse_file(path: Path) -> list[ManualTestCase]:
    if not path.is_file():
        raise IngestError(f"no such test-case file: {path}")

    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return parsers.parse_excel(path)

    text = path.read_text(encoding="utf-8", errors="replace")
    fmt = detect_format(path, text)
    handler = {
        "markdown": parsers.parse_markdown,
        "csv": parsers.parse_csv,
        "gherkin": parsers.parse_gherkin,
        "jira": parsers.parse_jira,
        "text": parsers.parse_text,
    }[fmt]
    return handler(text, str(path))


def ingest(root: Path, target: str | Path | None = None) -> IngestResult:
    """Parse one file, or every supported file under ``root``.

    A file that fails to parse is reported in ``errors`` rather than aborting the
    scan — one malformed export should not hide the rest of a suite.
    """
    result = IngestResult()
    base = resolve_within(root, target) if target else root.resolve()

    if base.is_file():
        paths = [base]
    elif base.is_dir():
        paths = sorted(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
    else:
        result.errors.append(f"no such path: {base}")
        return result

    seen: dict[str, int] = {}
    for path in paths:
        try:
            cases = parse_file(path)
        except Exception as exc:  # noqa: BLE001 - collected, not swallowed
            result.errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        result.files_read.append(str(path))
        for case in cases:
            # Ids must be unique across the whole scan or lookup is ambiguous.
            count = seen.get(case.id, 0)
            seen[case.id] = count + 1
            if count:
                case = case.model_copy(update={"id": f"{case.id}#{count + 1}"})
            result.cases.append(case)

    if not result.cases and not result.errors:
        result.errors.append(
            f"no test cases found under {base}. Supported extensions: "
            + ", ".join(sorted(SUFFIXES))
        )
    return result

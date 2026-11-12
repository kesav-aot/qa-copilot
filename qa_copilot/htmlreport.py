"""The report a person opens after a run.

One self-contained HTML file: what ran, what each step did, and a picture of the
page after it. Screenshots are embedded rather than linked, so the file can be
attached to a ticket or sent to someone and still show everything.

Everything written here has already been through the sanitiser — it is built
from the same report the model is given, and from screenshots in which every
credential field is masked. Text is escaped on the way in: a page title or an
error message comes from the application under test, and must not be able to
put markup into a file the QA engineer opens in their browser.
"""

from __future__ import annotations

import base64
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from qa_copilot.report import describe_step

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

_LABEL = {
    "passed": "Passed",
    "failed": "Failed",
    "error": "Could not run",
    "blocked": "Needs approval",
    "invalid": "Not a valid test",
}

# A report with hundreds of full-size pictures stops opening. Past this many
# bytes of images the rest are linked by path instead of embedded.
_EMBED_BUDGET = 60 * 1024 * 1024


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _duration(ms: Any) -> str:
    if not isinstance(ms, (int, float)):
        return ""
    if ms < 1000:
        return f"{int(ms)} ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f} s"
    return f"{int(ms // 60_000)} min {int(ms % 60_000 // 1000)} s"


class _Images:
    """Embeds screenshots until the budget runs out."""

    def __init__(self) -> None:
        self.spent = 0

    def tag(self, path: str | None, alt: str) -> str:
        if not path:
            return ""
        file = Path(path)
        try:
            size = file.stat().st_size
        except OSError:
            return f'<p class="missing">The screenshot is no longer on disk: {_e(path)}</p>'
        if self.spent + size > _EMBED_BUDGET:
            return f'<p class="missing">Screenshot kept on disk: {_e(path)}</p>'
        self.spent += size
        data = base64.b64encode(file.read_bytes()).decode("ascii")
        return (
            f'<figure class="shot" title="Click to enlarge"><img loading="lazy" '
            f'alt="{_e(alt)}" src="data:image/png;base64,{data}"></figure>'
        )


def _step_rows(test: dict[str, Any], images: _Images) -> str:
    report = test.get("report") or {}
    plan_steps = (test.get("plan") or {}).get("steps") or []
    rows: list[str] = []
    for entry in report.get("steps") or []:
        index = entry.get("index")
        source = plan_steps[index] if isinstance(index, int) and index < len(plan_steps) else {}
        text = describe_step(source) if source else str(entry.get("action", ""))
        status = entry.get("status", "error")
        number = index + 1 if isinstance(index, int) else ""
        detail = str(entry.get("detail") or "").strip()
        show_detail = status != "passed" and detail
        rows.append(
            f'<li class="step {_e(status)}">'
            f'<div class="line"><span class="n">{number}</span>'
            f'<span class="mark">{"✓" if status == "passed" else "✗"}</span>'
            f'<span class="what">{_e(text[:1].upper() + text[1:])}</span>'
            f'<span class="took">{_e(_duration(entry.get("duration_ms")))}</span></div>'
            + (f'<pre class="detail">{_e(detail)}</pre>' if show_detail else "")
            + images.tag(entry.get("screenshot"), f"After step {number}: {text}")
            + "</li>"
        )
    ran, total = report.get("steps_run", 0), report.get("steps_total", 0)
    if isinstance(ran, int) and isinstance(total, int) and total > ran:
        left = total - ran
        rows.append(
            f'<li class="step skipped"><div class="line"><span class="n"></span>'
            f'<span class="mark">·</span><span class="what">Stopped here — {left} later '
            f'step{"s" if left != 1 else ""} did not run</span></div></li>'
        )
    return "\n".join(rows)


def _test_section(test: dict[str, Any], number: int, images: _Images) -> str:
    report = test.get("report") or {}
    status = report.get("status", "error")
    name = test.get("name") or report.get("plan") or "Test"
    failure = report.get("failure") or {}
    policy = report.get("policy") or {}

    facts = [
        ("Environment", report.get("environment")),
        ("Address", report.get("base_url")),
        ("Started", report.get("started_at")),
        ("Took", _duration(report.get("duration_ms"))),
        ("Steps", f"{report.get('steps_run', 0)} of {report.get('steps_total', 0)}"
         if report.get("steps_total") else None),
        ("Risk", policy.get("risk") or report.get("risk")),
    ]
    facts_html = "".join(
        f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in facts if v
    )

    body = ""
    if status == "blocked":
        body = (
            '<div class="callout">This test was not run. It changes or removes data, so a '
            "person has to approve it first:"
            f"<pre>qa-copilot approve {_e(policy.get('fingerprint', ''))}</pre>"
            + "".join(f"<p>{_e(v)}</p>" for v in policy.get("violations") or [])
            + "</div>"
        )
    elif status == "invalid":
        body = '<div class="callout">' + "".join(
            f"<p>{_e(err)}</p>" for err in report.get("errors") or []
        ) + "</div>"
    else:
        if status != "passed" and failure.get("detail"):
            step_no = failure.get("step_index")
            where = f"Step {step_no + 1}" if isinstance(step_no, int) else "Before the first step"
            body += (
                f'<div class="callout"><strong>{_e(where)} went wrong.</strong>'
                f'<pre>{_e(failure.get("detail"))}</pre>'
            )
            console = failure.get("console_errors") or []
            if console:
                body += "<p>The page reported these errors:</p><pre>" + _e(
                    "\n".join(str(line) for line in console)
                ) + "</pre>"
            body += "</div>"
        body += f'<ol class="steps">{_step_rows(test, images)}</ol>'

    return (
        f'<section class="test {_e(status)}" id="test-{number}">'
        f'<details {"open" if status != "passed" or number == 1 else ""}>'
        f'<summary><span class="badge {_e(status)}">{_e(_LABEL.get(status, status))}</span>'
        f'<h2>{_e(name)}</h2></summary>'
        f'<dl class="facts">{facts_html}</dl>{body}</details></section>'
    )


_CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#16191d;--muted:#5d6673;--line:#e2e6ec;
--ok:#18794e;--ok-bg:#e6f6ec;--bad:#b3261e;--bad-bg:#fbeae8;--warn:#8a5a00;--warn-bg:#fff4d6}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--card:#1d2128;--ink:#e8ebf0;
--muted:#9aa4b2;--line:#2c323b;--ok:#4cc38a;--ok-bg:#12301f;--bad:#ff8a80;--bad-bg:#3a1917;
--warn:#f0c060;--warn-bg:#3a2e10}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:24px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:20px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile b{display:block;font-size:26px;line-height:1.2}
.tile span{color:var(--muted);font-size:13px}
.tile.passed b{color:var(--ok)}.tile.failed b,.tile.error b{color:var(--bad)}.tile.blocked b{color:var(--warn)}
table.index{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;margin-bottom:24px}
table.index td,table.index th{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:14px}
table.index tr:last-child td{border-bottom:0}
table.index a{color:inherit}
.test{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:16px;padding:4px 18px 14px}
summary{cursor:pointer;display:flex;align-items:center;gap:12px;padding:12px 0;list-style:none}
summary::-webkit-details-marker{display:none}
summary h2{font-size:18px;margin:0;overflow-wrap:anywhere}
.badge{flex:none;font-size:12px;font-weight:700;padding:3px 10px;border-radius:99px;white-space:nowrap}
.badge.passed{background:var(--ok-bg);color:var(--ok)}
.badge.failed,.badge.error,.badge.invalid{background:var(--bad-bg);color:var(--bad)}
.badge.blocked{background:var(--warn-bg);color:var(--warn)}
dl.facts{display:flex;flex-wrap:wrap;gap:6px 26px;margin:0 0 14px}
dl.facts dt{font-size:12px;color:var(--muted)}dl.facts dd{margin:0;overflow-wrap:anywhere}
.callout{background:var(--bad-bg);border-radius:8px;padding:12px 14px;margin-bottom:14px}
.test.blocked .callout{background:var(--warn-bg)}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.45 ui-monospace,Menlo,Consolas,monospace;margin:8px 0 0}
ol.steps{list-style:none;margin:0;padding:0}
.step{border-top:1px solid var(--line);padding:10px 0}
.line{display:flex;gap:10px;align-items:baseline}
.n{flex:none;width:22px;text-align:right;color:var(--muted);font-size:13px}
.mark{flex:none;font-weight:700}
.step.passed .mark{color:var(--ok)}.step.failed .mark,.step.error .mark{color:var(--bad)}
.what{flex:1;overflow-wrap:anywhere}
.took{flex:none;color:var(--muted);font-size:13px}
.detail{margin:8px 0 0 54px;color:var(--bad)}
.shot{display:block;margin:10px 0 2px 54px;max-width:560px;cursor:zoom-in}
.shot.big{max-width:none;margin-left:0;cursor:zoom-out}
.shot img{display:block;width:100%;border:1px solid var(--line);border-radius:8px}
.missing{margin:8px 0 0 54px;color:var(--muted);font-size:13px}
footer{color:var(--muted);font-size:13px;margin-top:24px}
@media (max-width:600px){.shot,.detail,.missing{margin-left:0}}
@media print{details{display:block}.shot{max-width:420px;break-inside:avoid}body{background:#fff}}
"""


def counts_of(tests: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for test in tests:
        status = (test.get("report") or {}).get("status", "error")
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_html(title: str, tests: list[dict[str, Any]], generated_at: str | None = None) -> str:
    """Render one HTML document for any number of test results.

    ``tests`` is a list of ``{"name", "plan", "report"}`` — exactly what a run
    keeps in its record.
    """
    generated_at = generated_at or datetime.now().astimezone().strftime("%d %b %Y, %H:%M")
    counts = counts_of(tests)
    total = len(tests)
    passed = counts.get("passed", 0)
    verdict = "All passed" if total and passed == total else f"{passed} of {total} passed"
    took = sum(
        (t.get("report") or {}).get("duration_ms") or 0
        for t in tests
        if isinstance((t.get("report") or {}).get("duration_ms"), (int, float))
    )

    tiles = [f'<div class="tile"><b>{total}</b><span>test{"s" if total != 1 else ""}</span></div>']
    for status in ("passed", "failed", "error", "blocked", "invalid"):
        if counts.get(status):
            tiles.append(
                f'<div class="tile {status}"><b>{counts[status]}</b>'
                f'<span>{_e(_LABEL[status].lower())}</span></div>'
            )
    tiles.append(f'<div class="tile"><b>{_e(_duration(took) or "—")}</b><span>total time</span></div>')

    index = ""
    if total > 1:
        rows = "".join(
            f'<tr><td><span class="badge {_e((t.get("report") or {}).get("status", "error"))}">'
            f'{_e(_LABEL.get((t.get("report") or {}).get("status", "error"), "?"))}</span></td>'
            f'<td><a href="#test-{i}">{_e(t.get("name") or (t.get("report") or {}).get("plan"))}</a></td>'
            f'<td>{_e(_duration((t.get("report") or {}).get("duration_ms")))}</td></tr>'
            for i, t in enumerate(tests, 1)
        )
        index = f'<table class="index"><tbody>{rows}</tbody></table>'

    images = _Images()
    sections = "\n".join(_test_section(t, i, images) for i, t in enumerate(tests, 1))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} — QA Copilot report</title>
<style>{_CSS}</style></head>
<body><main>
<h1>{_e(title)}</h1>
<p class="sub">{_e(verdict)} · {_e(generated_at)}</p>
<div class="tiles">{"".join(tiles)}</div>
{index}
{sections}
<footer>Made by QA Copilot. Password fields are masked in every screenshot, and no
credential is written to this file. Click a screenshot to enlarge it.</footer>
</main>
<script>document.addEventListener("click",function(e){{var f=e.target.closest(".shot");if(f)f.classList.toggle("big")}})</script>
</body></html>
"""


class ReportStore:
    """Run records and the HTML reports made from them.

    A record is the sanitised result of one run, kept as JSON beside its
    report. Keeping it is what lets a report be rebuilt later, or several runs
    be put into one report, without running anything again.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _record_path(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            # The id arrives from a model. It names a file, so it must look
            # exactly like an id this class made and nothing else.
            raise ValueError(f"{run_id!r} is not a run id")
        return self.root / "runs" / f"{run_id}.json"

    def save(
        self, run_id: str, title: str, kind: str, tests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Keep the record and write its report. Returns where they went."""
        record = {
            "run_id": run_id,
            "title": title,
            "kind": kind,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "counts": counts_of(tests),
            "tests": tests,
        }
        path = self._record_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        return self._write_html(run_id, title, tests)

    def _write_html(self, name: str, title: str, tests: list[dict[str, Any]]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:50] or "report"
        out = self.root / f"{name}-{slug}.html"
        out.write_text(build_html(title, tests), encoding="utf-8")
        return {
            "path": str(out),
            "link": out.as_uri(),
            "title": title,
            "tests": len(tests),
            "counts": counts_of(tests),
        }

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._record_path(run_id)
        if not path.is_file():
            raise FileNotFoundError(f"there is no run called {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        """Most recent first."""
        folder = self.root / "runs"
        if not folder.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json"), reverse=True):
            if not RUN_ID.match(path.stem):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(
                {
                    "run_id": record.get("run_id", path.stem),
                    "title": record.get("title"),
                    "kind": record.get("kind"),
                    "created_at": record.get("created_at"),
                    "counts": record.get("counts", {}),
                    "tests": [
                        {
                            "name": t.get("name"),
                            "status": (t.get("report") or {}).get("status"),
                        }
                        for t in record.get("tests", [])
                    ],
                }
            )
            if len(out) >= limit:
                break
        return out

    def combine(self, run_ids: list[str], title: str) -> dict[str, Any]:
        """One report from several earlier runs, in the order given."""
        tests: list[dict[str, Any]] = []
        for run_id in run_ids:
            tests.extend(self.load(run_id).get("tests", []))
        from qa_copilot.executor.runner import new_run_id

        result = self._write_html(f"{new_run_id()}-combined", title, tests)
        result["runs"] = run_ids
        return result

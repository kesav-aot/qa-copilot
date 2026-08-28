"""Find an element from an ordinary English phrase, against the live page.

This is what lets a QA engineer write ``Click the Save button`` instead of
``page.locator("#save-btn")``. The phrase is not compiled to a selector in
advance — it is resolved against the page as it actually is, at the moment the
step runs.

Two design rules, both about the failure path, because that is where a
non-coding user either gets unblocked or gives up:

* **Never silently pick.** If a phrase matches three things, say which three and
  how to disambiguate. A test that quietly clicked the wrong "Delete" is worse
  than one that stopped.
* **Never just say "not found".** Say what *is* on the page, so the next attempt
  is an edit rather than a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from qa_copilot.dsl.schema import Target

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Locator, Page


class ResolutionError(RuntimeError):
    """Base for 'I could not turn your words into one element on this page'."""


class ElementNotFound(ResolutionError):
    pass


class ElementAmbiguous(ResolutionError):
    pass


# A trailing noun tells us what kind of thing to look for. Longest first so
# "menu item" wins over "item".
_ROLE_WORDS: list[tuple[str, str]] = [
    ("menu item", "menuitem"),
    ("radio button", "radio"),
    ("check box", "checkbox"),
    ("checkbox", "checkbox"),
    ("dropdown", "combobox"),
    ("drop down", "combobox"),
    ("select", "combobox"),
    ("textarea", "textbox"),
    ("text box", "textbox"),
    ("textbox", "textbox"),
    ("input", "textbox"),
    ("field", "textbox"),
    ("button", "button"),
    ("link", "link"),
    ("tab", "tab"),
    ("heading", "heading"),
    ("title", "heading"),
    ("option", "option"),
    ("row", "row"),
    ("image", "img"),
    ("icon", "icon"),
    ("box", "textbox"),
]

_ARTICLES = re.compile(r"(?i)^\s*(?:the|a|an|its|this|that)\s+")
_QUOTED = re.compile(r"^[\"'“”‘’](.*)[\"'“”‘’]$")
_ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1,
}
_ORDINAL_RE = re.compile(
    r"(?i)^\s*(?:the\s+)?(" + "|".join(_ORDINALS) + r")\s+(.*)$"
)

# HTML tag names mean nothing to a QA engineer.
_FRIENDLY_TAG = {
    "a": "link",
    "button": "button",
    "select": "dropdown",
    "textarea": "text box",
    "h1": "heading", "h2": "heading", "h3": "heading",
    "h4": "heading", "h5": "heading", "h6": "heading",
    "img": "image",
    "label": "label",
    "td": "table cell", "th": "table heading", "tr": "table row",
    "li": "list item",
}

_MAX_CANDIDATES_DESCRIBED = 6
_INVENTORY_LIMIT = 400   # elements scanned
_INVENTORY_PER_KIND = 12  # shown per kind


@dataclass
class Phrase:
    """What we understood from the words."""

    name: str
    role: str | None
    index: int | None

    def describe(self) -> str:
        bits = [f'"{self.name}"'] if self.name else ["anything"]
        if self.role:
            bits.append(f"({self.role})")
        return " ".join(bits)


def parse_phrase(text: str, index: int | None = None) -> Phrase:
    """``the second Disable button`` → name='Disable', role='button', index=2."""
    working = (text or "").strip()

    match = _ORDINAL_RE.match(working)
    if match and index is None:
        index = _ORDINALS[match.group(1).lower()]
        working = match.group(2).strip()

    working = _ARTICLES.sub("", working).strip()

    role = None
    lowered = working.lower()
    for word, mapped in _ROLE_WORDS:
        if lowered.endswith(" " + word) or lowered == word:
            role = mapped
            working = working[: len(working) - len(word)].strip()
            break

    working = _ARTICLES.sub("", working).strip()
    quoted = _QUOTED.match(working)
    if quoted:
        working = quoted.group(1)

    return Phrase(name=working.strip(" .:"), role=role, index=index)


# --- candidate generation --------------------------------------------------

_ROW_SELECTOR = "tr, li, [role=row], [role=listitem], article, section, .row, .card"


async def _scope(page: "Page", within: str | None):
    """Narrow to the table row / list item / card containing some text.

    Rows nest: a <tr> holding an inner table contains every inner row's text, so
    a plain contains-text match also selects the ancestor — and scoping to that
    silently widens the search back to the whole table, which is the opposite of
    what the author asked for. Prefer the innermost match: a container with no
    matching container inside it.
    """
    if not within:
        return page
    rows = page.locator(_ROW_SELECTOR).filter(has_text=within)
    innermost = rows.filter(has_not=page.locator(_ROW_SELECTOR).filter(has_text=within))
    try:
        if await innermost.count() > 0:
            return innermost
    except Exception:
        pass
    return rows


def _icon_strategies(root, name: str) -> list[tuple[str, Any]]:
    """Match an icon by its alt text, title, or image filename.

    Icon-only controls carry no text, so every text- and role-based strategy
    misses them. The image filename is the last resort and is often the only
    human-meaningful name an icon has (``robot.png`` → "robot").
    """
    safe = name.replace("\\", "").replace('"', "")
    if not safe:
        return []
    img = f'img[alt*="{safe}" i], img[title*="{safe}" i], img[src*="{safe}" i]'
    # Clicking the image works whether or not a link wraps it — the event bubbles.
    wrapper = ", ".join(
        f"{tag}:has({img})" for tag in ("a", "button", "[role=button]", "[onclick]")
    )
    return [
        ("icon image", root.locator(img)),
        ("clickable icon", root.locator(wrapper)),
    ]


def _strategies(root, phrase: Phrase) -> list[tuple[str, "Locator"]]:
    """Ordered attempts. Earlier entries are more trustworthy.

    When the phrase names a kind of thing ("the Save **button**"), only that kind
    is searched. Falling back to a link called Save would be the silent-wrong-pick
    this module exists to avoid — so instead the caller reports the mismatch.
    """
    name = phrase.name
    out: list[tuple[str, Any]] = []
    if not name:
        if phrase.role:
            out.append((f"any {phrase.role}", root.get_by_role(phrase.role)))
        return out

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    out.append(("test id", root.get_by_test_id(slug)))

    if phrase.role == "icon":
        # Superset of the old behaviour, which treated every icon as a button.
        out.extend(_icon_strategies(root, name))
        out.append(("button named", root.get_by_role("button", name=name)))
        out.append(("link named", root.get_by_role("link", name=name)))
        out.append(("title/alt", root.get_by_title(name)))
        return out

    if phrase.role:
        out.append((f"{phrase.role} named exactly", root.get_by_role(phrase.role, name=name, exact=True)))
        out.append((f"{phrase.role} named", root.get_by_role(phrase.role, name=name)))
        return out

    for role in ("button", "link", "textbox", "checkbox", "combobox"):
        out.append((f"{role} named exactly", root.get_by_role(role, name=name, exact=True)))
    for role in ("button", "link", "textbox", "checkbox", "combobox"):
        out.append((f"{role} named", root.get_by_role(role, name=name)))

    out.append(("labelled field", root.get_by_label(name)))
    out.append(("placeholder", root.get_by_placeholder(name)))
    out.append(("exact text", root.get_by_text(name, exact=True)))
    out.append(("title/alt", root.get_by_title(name)))
    out.append(("text", root.get_by_text(name)))
    out.extend(_icon_strategies(root, name))
    return out


async def _wrong_kind_hint(root, phrase: Phrase) -> str | None:
    """If the name exists but as a different kind of thing, say so — that is
    almost always the actual mistake."""
    if not phrase.role or not phrase.name:
        return None
    for locator in (
        root.get_by_text(phrase.name, exact=True),
        root.get_by_label(phrase.name),
        root.get_by_text(phrase.name),
    ):
        matches = await _visible_matches(locator, cap=3)
        if matches:
            found = await _describe_element(locator.nth(matches[0]))
            return (
                f'I did find {found} — but you asked for a {phrase.role}, and that '
                f"is not one.\n  If that is the thing you meant, drop the word "
                f'"{phrase.role}" from the step.'
            )
    return None


async def _visible_matches(locator: "Locator", cap: int = 12) -> list[int]:
    try:
        total = await locator.count()
    except Exception:
        return []
    found = []
    for i in range(min(total, cap)):
        try:
            if await locator.nth(i).is_visible():
                found.append(i)
        except Exception:
            continue
    return found


async def _describe_element(locator: "Locator") -> str:
    """A one-line, human-readable description of a candidate."""
    try:
        info = await locator.evaluate(
            """el => {
                const row = el.closest('tr, li, [role=row], [role=listitem]');
                let label = (el.innerText || el.value || el.getAttribute('aria-label')
                               || el.getAttribute('placeholder') || el.getAttribute('title')
                               || '').trim().replace(/\\s+/g, ' ').slice(0, 50);
                if (!label) {
                    const img = el.tagName.toLowerCase() === 'img' ? el : el.querySelector('img');
                    if (img) {
                        const src = img.getAttribute('src') || '';
                        label = (img.getAttribute('alt') || img.getAttribute('title') || '').trim()
                                || ((src.split('/').pop() || '').split('?')[0]
                                    .replace(/\\.(png|gif|jpe?g|svg|webp)$/i, ''));
                    }
                }
                return {
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type'),
                    testid: el.getAttribute('data-testid'),
                    label: label,
                    row: row && row !== el
                        ? row.innerText.trim().replace(/\\s+/g, ' ').slice(0, 40)
                        : null,
                };
            }"""
        )
    except Exception:
        return "an element"

    kind = _FRIENDLY_TAG.get(info.get("tag") or "", info.get("type") or info.get("tag") or "element")
    out = f'"{info["label"]}" ({kind})' if info.get("label") else f"a {kind}"
    if info.get("row"):
        out += f' in the row "{info["row"]}"'
    if info.get("testid"):
        out += f'  [test id: {info["testid"]}]'
    return out


# --- what is actually on this page -----------------------------------------

async def page_inventory(page: "Page") -> dict[str, list[str]]:
    """Everything a person could plausibly act on, grouped by kind.

    Attached to every not-found error, because "here is what is here" turns a
    dead end into an edit.
    """
    try:
        raw = await page.evaluate(
            """() => {
                const seen = new Set();
                const out = [];
                const sel = 'a,button,input,select,textarea,[role=button],[role=link],[role=tab],h1,h2,h3,img';
                // An icon carries no text. Name it by alt/title, else by its filename.
                const iconName = (el) => {
                    const img = el.tagName.toLowerCase() === 'img' ? el : el.querySelector('img');
                    if (!img) return '';
                    const named = (img.getAttribute('alt') || img.getAttribute('title') || '').trim();
                    if (named) return named;
                    const src = img.getAttribute('src') || '';
                    const file = (src.split('/').pop() || '').split('?')[0]
                                 .replace(/\\.(png|gif|jpe?g|svg|webp)$/i, '');
                    return file ? 'icon: ' + file : '';
                };
                for (const el of Array.from(document.querySelectorAll(sel))) {
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (!el.getClientRects().length) continue;
                    const tag = el.tagName.toLowerCase();
                    const type = el.getAttribute('type');
                    let kind = tag;
                    if (tag === 'a') kind = 'link';
                    else if (tag === 'button' || type === 'submit' || type === 'button') kind = 'button';
                    else if (tag === 'select') kind = 'dropdown';
                    else if (tag === 'input' || tag === 'textarea') kind = type === 'checkbox'
                        ? 'checkbox' : (type === 'password' ? 'password field' : 'field');
                    else if (/^h[1-3]$/.test(tag)) kind = 'heading';
                    if (tag === 'img') kind = 'icon';
                    let label = (el.innerText || el.getAttribute('aria-label')
                                   || el.getAttribute('placeholder') || el.getAttribute('name')
                                   || el.getAttribute('title') || '').trim()
                                  .replace(/\\s+/g, ' ').slice(0, 45);
                    if (!label) {
                        label = iconName(el);
                        if (label) kind = 'icon';
                    }
                    if (!label) continue;
                    const testid = el.getAttribute('data-testid');
                    const key = kind + '|' + label;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    out.push({kind, label, testid});
                }
                return out;
            }"""
        )
    except Exception:
        return {}

    grouped: dict[str, list[str]] = {}
    for item in raw[:_INVENTORY_LIMIT]:
        entry = f'"{item["label"]}"'
        if item.get("testid"):
            entry += f'  [test id: {item["testid"]}]'
        entries = grouped.setdefault(item["kind"], [])
        if len(entries) < _INVENTORY_PER_KIND:
            entries.append(entry)
    return grouped


def format_inventory(grouped: dict[str, list[str]]) -> str:
    if not grouped:
        return "  (nothing interactive found on the page)"
    lines = []
    for kind in sorted(grouped):
        lines.append(f"  {kind}s you can use here:")
        lines.extend(f"    - {entry}" for entry in grouped[kind][:_INVENTORY_PER_KIND])
    return "\n".join(lines)


# --- the entry point --------------------------------------------------------

async def resolve(page: "Page", target: Target) -> "Locator":
    """Turn a Target into exactly one Playwright locator, or explain why not.

    Searches the main document first, then any iframes. Apps that render a
    document or editor in a frame are otherwise invisible: the element is on
    screen, the person can see it, and the tool insists it does not exist.
    """
    # Precise targets stay precise — no guessing when the author was specific.
    if not target.describe:
        return _explicit(page, target)

    try:
        return await _resolve_within(page, target)
    except ElementNotFound as not_in_main:
        try:
            frames = [f for f in page.frames if f is not page.main_frame]
        except Exception:
            frames = []
        for frame in frames:
            try:
                return await _resolve_within(frame, target)
            except ResolutionError:
                continue  # ambiguity inside a frame is still that frame's problem
        if frames:
            raise ElementNotFound(
                f"{not_in_main}\n\n  I also looked inside {len(frames)} iframe(s) "
                f"on this page and did not find it there either."
            ) from not_in_main
        raise


async def _resolve_within(page: "Page", target: Target) -> "Locator":
    """Resolve against one document — the main page, or a single frame."""
    phrase = parse_phrase(target.describe, target.index)
    root = await _scope(page, target.within)

    if target.within:
        try:
            if await root.count() == 0:
                raise ElementNotFound(
                    f'I could not find a row or section containing "{target.within}" '
                    f"on {page.url}.\n" + format_inventory(await page_inventory(page))
                )
        except ResolutionError:
            raise
        except Exception:
            pass

    tried: list[str] = []
    for how, locator in _strategies(root, phrase):
        matches = await _visible_matches(locator)
        tried.append(how)
        if not matches:
            continue

        if phrase.index is not None:
            wanted = matches[-1] if phrase.index == -1 else phrase.index - 1
            if wanted < 0 or wanted >= len(matches):
                raise ElementNotFound(
                    f"You asked for match {phrase.index} of {phrase.describe()}, "
                    f"but I only found {len(matches)} on {page.url}."
                )
            return locator.nth(matches[wanted])

        if len(matches) == 1:
            return locator.nth(matches[0])

        described = []
        for i in matches[:_MAX_CANDIDATES_DESCRIBED]:
            described.append(f"  {len(described) + 1}. " + await _describe_element(locator.nth(i)))
        more = "" if len(matches) <= _MAX_CANDIDATES_DESCRIBED else f"\n  … and {len(matches) - _MAX_CANDIDATES_DESCRIBED} more"
        raise ElementAmbiguous(
            f"{phrase.describe()} matches {len(matches)} things on this page, so I "
            f"stopped rather than guess:\n" + "\n".join(described) + more + "\n\n"
            f"Narrow it down, for example:\n"
            f'  - "the first {target.describe}"\n'
            f'  - "{target.describe} in the <row text> row"\n'
            f"  - use the exact wording on the element"
        )

    hint = await _wrong_kind_hint(root, phrase)
    if hint:
        raise ElementNotFound(
            f"I could not find {phrase.describe()} on {page.url}.\n  {hint}"
        )
    raise ElementNotFound(
        f"I could not find {phrase.describe()} on {page.url}.\n"
        + format_inventory(await page_inventory(page))
        + "\n\n  Check the wording matches what is on screen, or take a screenshot "
        "first to see where you ended up."
    )


def _explicit(page: "Page", target: Target) -> "Locator":
    if target.testid:
        return page.get_by_test_id(target.testid)
    if target.role:
        kwargs: dict[str, Any] = {}
        if target.name:
            kwargs["name"] = target.name
        return page.get_by_role(target.role, **kwargs)  # type: ignore[arg-type]
    if target.label:
        return page.get_by_label(target.label)
    if target.text:
        return page.get_by_text(target.text)
    if target.css:
        return page.locator(target.css)
    raise ElementNotFound(f"cannot resolve target: {target.summary()}")

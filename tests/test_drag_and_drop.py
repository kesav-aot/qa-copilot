"""Dragging, against a builder that behaves like the real ones.

The demo builder ignores a pointer that jumps from the palette to the canvas,
as dragula and diagram-js do. A drag that passes here is one made of real,
stepped pointer movement.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qa_copilot.dsl.schema import TestPlan as Plan
from qa_copilot.plain.writer import step_to_english


def _plan(*steps) -> dict:
    return {"version": 1, "name": "drag", "environment": "demo", "steps": list(steps)}


async def test_a_component_is_dragged_onto_the_canvas_in_plain_english(copilot):
    result = await copilot.run_plain(
        "# Build a form\n\n"
        "Go to /builder\n"
        'Drag "Text Field" onto the form area\n'
        'Check the page shows "Text Field added"\n'
        'Drag "Checkbox" onto the form area\n'
        'Check the page shows "2 components"\n'
    )
    assert result["understood"], result
    assert result["overall"] == "passed", result["results"][0]["report"]


async def test_the_drop_lands_where_the_offsets_say(copilot):
    report = await copilot.run(
        _plan(
            {"action": "navigate", "path": "/builder"},
            {
                "action": "drag",
                "target": {"css": '.component[data-type="number"]'},
                "to": {"css": "#canvas"},
                "offset_x": 300,
                "offset_y": 120,
            },
            {"action": "assert", "kind": "visible",
             "target": {"css": '.placed[data-type="number"][data-x="300"][data-y="120"]'}},
        )
    )
    assert report["status"] == "passed", report


async def test_a_selector_can_stand_in_for_something_with_no_name(copilot):
    result = await copilot.run_plain(
        "# Build a form\n\nGo to /builder\n"
        "Drag css=.component[data-type=number] onto css=#canvas at 200, 100\n"
        'Check the page shows "Number added"\n'
    )
    assert result["overall"] == "passed", result


async def test_dragging_something_that_is_not_there_says_what_is(copilot):
    result = await copilot.run_plain(
        '# Build a form\n\nGo to /builder\nDrag "Signature" onto the form area\n'
        'Check the page shows "Signature added"\n'
    )
    report = result["results"][0]["report"]
    assert report["status"] == "failed"
    assert "Signature" in report["failure"]["detail"]


def test_a_drag_needs_both_ends():
    with pytest.raises(ValidationError):
        Plan.model_validate(_plan({"action": "drag", "target": {"describe": "Text Field"}}))


def test_a_drag_reads_back_as_the_sentence_that_made_it():
    step = {
        "action": "drag",
        "target": {"describe": "Text Field"},
        "to": {"describe": "the form area"},
    }
    assert step_to_english(step) == 'Drag "Text Field" onto the form area'
    placed = {
        "action": "drag",
        "target": {"css": ".entry"},
        "to": {"css": ".canvas"},
        "offset_x": 300,
        "offset_y": 200,
    }
    assert step_to_english(placed) == "Drag css=.entry onto css=.canvas at 300, 200"


def test_a_drop_target_selector_is_subject_to_the_selector_policy(copilot):
    copilot.config.policy.allow_css_selectors = False
    verdict = copilot.validate(
        _plan(
            {"action": "navigate", "path": "/builder"},
            {"action": "drag", "target": {"describe": "Text Field"}, "to": {"css": "#canvas"}},
            {"action": "assert", "kind": "text", "expected": "Text Field added"},
        )
    )
    assert not verdict["policy"]["allowed"]
    assert "CSS selector" in verdict["policy"]["violations"][0]


async def test_a_browser_confirmation_is_accepted_and_reported(copilot):
    """Left alone, the browser dismisses an "Are you sure?" — which cancels the
    very thing the step clicked, and the test then fails for no visible reason."""
    result = await copilot.run_plain(
        "# Clear the form\n\nGo to /builder\n"
        'Drag "Number" onto the form area\n'
        'Check the page shows "1 component"\n'
        "Click the Clear form button\n"
        'Check the page shows "0 components"\n'
    )
    report = result["results"][0]["report"]
    assert report["status"] == "passed", report
    assert "Are you sure you want to remove every component?" in report["steps"][3]["detail"]


async def test_waiting_can_use_a_selector(copilot):
    result = await copilot.run_plain(
        "# Wait\n\nGo to /builder\nWait for css=#canvas\nCheck I can see css=.component >> nth=0\n"
    )
    assert result["overall"] == "passed", result

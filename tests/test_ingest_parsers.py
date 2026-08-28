"""Parsers must be forgiving about formatting and strict about where they read."""

from __future__ import annotations

from pathlib import Path

import pytest

from qa_copilot.ingest import loader, parsers
from qa_copilot.ingest.loader import IngestError

ROOT = Path(__file__).resolve().parents[1]
TESTCASES = ROOT / "testcases"


# --- markdown --------------------------------------------------------------

MARKDOWN = """
# TC-500: Admin can archive a record

Description: Checks archiving.

Preconditions:
- Logged in as an administrator.

Steps:
1. Navigate to the Records page
2. Click the "Archive" button

Expected results:
- The record shows "archived"

Tags: records, admin
Priority: High
"""


def test_markdown_sections_and_id():
    case = parsers.parse_markdown(MARKDOWN, "x.md")[0]
    assert case.id == "TC-500"
    assert case.title == "Admin can archive a record"
    assert case.preconditions == ["Logged in as an administrator."]
    assert [s.action for s in case.steps] == [
        "Navigate to the Records page",
        'Click the "Archive" button',
    ]
    assert case.expected_results == ['The record shows "archived"']
    assert case.tags == ["records", "admin"]
    assert case.priority == "High"


def test_markdown_accepts_bold_labels_and_inline_expectations():
    text = """
# TC-501: Bold labels

**Steps:**
1. Open the Dashboard -> the dashboard loads
2. Click Sign out | the login page appears
"""
    case = parsers.parse_markdown(text, "x.md")[0]
    assert len(case.steps) == 2
    assert case.steps[0].action == "Open the Dashboard"
    assert case.steps[0].expected == "the dashboard loads"
    assert case.steps[1].expected == "the login page appears"


def test_markdown_splits_multiple_cases():
    assert len(parsers.parse_markdown(MARKDOWN + MARKDOWN.replace("TC-500", "TC-502"), "x.md")) == 2


# --- csv -------------------------------------------------------------------

def test_csv_column_aliases_and_embedded_newlines():
    text = (
        'Test ID,Summary,Preconditions,Steps,Expected Result,Labels\n'
        'TC-600,Login works,None,"1. Navigate to Login\n2. Sign in","The dashboard loads",smoke\n'
    )
    case = parsers.parse_csv(text, "x.csv")[0]
    assert case.id == "TC-600"
    assert [s.action for s in case.steps] == ["Navigate to Login", "Sign in"]
    assert case.expected_results == ["The dashboard loads"]
    assert case.tags == ["smoke"]


def test_csv_with_semicolon_delimiter():
    text = "id;title;steps;expected\nTC-601;Thing;1. Do it;It happened\n"
    case = parsers.parse_csv(text, "x.csv")[0]
    assert case.id == "TC-601" and case.steps[0].action == "Do it"


def test_csv_without_recognisable_columns_is_an_error():
    with pytest.raises(ValueError, match="could not find a title or steps column"):
        parsers.parse_csv("alpha,beta\n1,2\n", "x.csv")


# --- gherkin ---------------------------------------------------------------

def test_gherkin_maps_given_when_then():
    text = """
@regression
Feature: Sign in

  @smoke
  Scenario: Customer signs in
    Given the customer has an account
    When they submit the sign-in form
    And they wait for the redirect
    Then the dashboard is shown
    And the URL contains /dashboard
"""
    case = parsers.parse_gherkin(text, "x.feature")[0]
    assert case.preconditions == ["the customer has an account"]
    assert [s.action for s in case.steps] == [
        "they submit the sign-in form",
        "they wait for the redirect",
    ]
    assert case.expected_results == ["the dashboard is shown", "the URL contains /dashboard"]
    assert case.tags == ["smoke"]
    assert case.description == "Sign in"


def test_gherkin_handles_several_scenarios():
    text = (
        "Feature: F\n"
        "  Scenario: One\n    When a\n    Then b\n"
        "  Scenario: Two\n    When c\n    Then d\n"
    )
    assert [c.title for c in parsers.parse_gherkin(text, "x.feature")] == ["One", "Two"]


# --- jira ------------------------------------------------------------------

def test_jira_export_uses_the_issue_key_as_the_id():
    cases = parsers.parse_jira((TESTCASES / "jira-export.json").read_text(), "jira.json")
    assert [c.id for c in cases] == ["QA-4417", "QA-4418"]
    assert cases[0].tags == ["authz", "regression"]
    assert cases[0].priority == "High"
    assert cases[0].steps


def test_jira_flattens_atlassian_document_format():
    payload = (
        '{"issues":[{"key":"QA-1","fields":{"summary":"S","description":'
        '{"type":"doc","content":[{"type":"paragraph","content":['
        '{"type":"text","text":"Steps:"}]},{"type":"paragraph","content":['
        '{"type":"text","text":"1. Navigate to Home"}]}]}}}]}'
    )
    case = parsers.parse_jira(payload, "jira.json")[0]
    assert case.steps[0].action == "Navigate to Home"


def test_jira_rejects_a_payload_that_is_not_an_export():
    with pytest.raises(ValueError, match="issues"):
        parsers.parse_jira('{"foo": 1}', "x.json")


# --- excel -----------------------------------------------------------------

def test_excel_round_trip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "cases.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Test ID", "Summary", "Steps", "Expected Result"])
    ws.append(["TC-700", "Excel case", "1. Navigate to Home\n2. Click Save", "It saved"])
    ws.append([None, None, None, None])  # blank rows must be skipped
    wb.save(path)

    case = parsers.parse_excel(path)[0]
    assert case.id == "TC-700" and case.format == "excel"
    assert [s.action for s in case.steps] == ["Navigate to Home", "Click Save"]


# --- detection and scanning ------------------------------------------------

def test_detect_format_by_suffix():
    assert loader.detect_format(Path("a.md")) == "markdown"
    assert loader.detect_format(Path("a.feature")) == "gherkin"
    assert loader.detect_format(Path("a.json")) == "jira"


def test_detect_format_sniffs_gherkin_inside_a_markdown_file():
    assert loader.detect_format(Path("a.md"), "Feature: X\n  Scenario: Y\n") == "gherkin"


def test_unsupported_suffix_is_reported_clearly():
    with pytest.raises(IngestError, match="unsupported file type"):
        loader.detect_format(Path("a.docx"))


def test_scanning_the_bundled_test_cases_reads_every_format():
    result = loader.ingest(TESTCASES)
    assert not result.errors
    formats = {c.format for c in result.cases}
    assert {"markdown", "csv", "gherkin", "jira"} <= formats
    assert len({c.id for c in result.cases}) == len(result.cases), "ids must be unique"


def test_duplicate_ids_across_files_are_disambiguated(tmp_path):
    (tmp_path / "a.md").write_text("# TC-1: One\nSteps:\n1. Navigate to Home\n")
    (tmp_path / "b.md").write_text("# TC-1: Two\nSteps:\n1. Navigate to Home\n")
    ids = [c.id for c in loader.ingest(tmp_path).cases]
    assert ids == ["TC-1", "TC-1#2"]


def test_one_broken_file_does_not_hide_the_others(tmp_path):
    (tmp_path / "good.md").write_text("# TC-1: Fine\nSteps:\n1. Navigate to Home\n")
    (tmp_path / "bad.json").write_text("{not json")
    result = loader.ingest(tmp_path)
    assert [c.id for c in result.cases] == ["TC-1"]
    assert any("bad.json" in e for e in result.errors)


def test_path_traversal_is_refused(tmp_path):
    with pytest.raises(IngestError, match="outside the test-case directory"):
        loader.resolve_within(tmp_path, "../../etc/passwd")


def test_absolute_path_outside_the_root_is_refused(tmp_path):
    with pytest.raises(IngestError, match="outside the test-case directory"):
        loader.resolve_within(tmp_path, "/etc/passwd")


def test_a_path_inside_the_root_is_allowed(tmp_path):
    (tmp_path / "sub").mkdir()
    assert loader.resolve_within(tmp_path, "sub").name == "sub"


def test_empty_directory_says_so_rather_than_returning_silence(tmp_path):
    result = loader.ingest(tmp_path)
    assert result.cases == []
    assert any("no test cases found" in e for e in result.errors)

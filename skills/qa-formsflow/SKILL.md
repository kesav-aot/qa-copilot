---
name: qa-formsflow
description: Test formsflow.ai (AOT Technologies forms-flow-ai) — the form.io drag-and-drop form designer, the BPMN and DMN editors, publishing, client submissions, reviewer tasks and premium bundles. Use whenever the application under test is formsflow or a product built on it, or the request mentions forms-flow, form.io components, dragging a component, a BPMN/subflow/decision table, a bundle, or Keycloak sign-in to formsflow.
---

# Testing formsflow.ai

formsflow is not a page of buttons and fields. Its two main screens are
**editors driven by drag and drop** — the form.io form builder and the bpmn-js
diagram editor — its dropdowns are not real dropdowns, and it signs in through
Keycloak on another address. A test written as if it were an ordinary web app
fails on the first interesting step. This skill is the difference.

> **Where this comes from.** The selectors, addresses and wording below were
> read from the formsflow source (8.x `develop`, with 7.3.0 differences noted),
> not checked against your installation. Labels move between releases. When a
> step fails, trust the failure detail — it lists what is really on the page —
> fix the test, and record the correction in a project skill
> (`qa-project-skill`) so it is right next time.

## Ground rules for every formsflow test

1. **Prefer test ids to labels.** formsflow's text is translated and changes
   between releases; its `data-testid` values are steadier. A test id can be
   written as a name: `Click save-form-layout`.
2. **Wait for the page, not for the load.** formsflow is several small apps
   mounted separately; the menu arrives before the content. After every move,
   `Wait for <something on that page>`.
3. **A grey overlay blocks clicks.** While "Loading..." shows, clicks land on
   the overlay. Wait for the thing you want, never a fixed number of seconds.
4. **Quote component names** in a drag: `Drag "Text Field" onto …`. Unquoted,
   "Text Field" is read as *a field called Text*.
5. **Evidence is automatic.** Every step is photographed and the run ends with
   an HTML report. For an editor that is the proof the component landed —
   always give the user the report (`qa-report`).
6. **Designing changes data.** Saving, publishing and deleting need approval
   (`qa-copilot approve <fingerprint>`) like any other data-changing test. Use
   a test form with an obvious name, never a form real users rely on.

## Signing in

Sign-in is Keycloak, usually on a **different host** from the web app. Setup
(`open_setup`) handles that; the form's fields are `#username`, `#password` and
the button `#kc-login`. Never ask for a password — ask which test account.

Before opening setup, tell the person what to enter there:

- **Address of your app** — the formsflow web address, e.g.
  `https://forms-flow-web-qa.example.com`.
- **Sign-in page** — `/`. formsflow sends the browser on to Keycloak by
  itself, and setup follows it.
- **Nickname** — the role, e.g. `designer`, so the alias reads `DESIGNER_USER`.
- **Anything else it can do** — `design_forms` for a designer,
  `review_tasks` for a reviewer, `submit_forms` for a client. Without it,
  nothing marks the account as able to build forms.

Setup only reports success once the browser is back inside formsflow. If it
says the browser stayed on Keycloak, the account did not sign in — wrong
password, or Keycloak wants a new password or a one-time code first — and
nothing was saved. The new account is usable straight away: there is no need
to restart Claude Desktop, and `list_identities` shows it on the next call.

formsflow decides what a person sees from permissions, which come from groups:

| Group | Lands on | Sees |
|---|---|---|
| `formsflow-designer` | `/formflow` | Build → Forms, Subflows, Decision Tables |
| `formsflow-reviewer` | `/task` | Tasks; Analyze → Metrics, Dashboards |
| `formsflow-client` | `/form` | Submit |
| `formsflow-admin` | `/admin` | Manage |

A stock install has only an admin user: designer, reviewer and client test
accounts have to be created in Keycloak and added through setup. If
`list_identities` shows no designer, say so rather than testing the designer as
an admin. In a test, `Log in as a designer`, `as a reviewer` and `as a client`
pick the accounts holding `design_forms`, `review_tasks` and `submit_forms`.

A multi-tenant install puts `/tenant/<key>` in front of every address below.

## Where things are

| Page | Address | Wait for |
|---|---|---|
| Form list (designer) | `/formflow` | `create-form-button` |
| Ways to create a form | `/formflow/build` | |
| New form | `/formflow/create` | `header-form-title-input` |
| Edit a form | `/formflow/<formId>/edit` | `save-form-layout` |
| Subflows (BPMN) | `/subflow`, `/subflow/create`, `/subflow/edit/<key>` | `bpmneditor-canvas` |
| Decision tables (DMN) | `/decision-table`, `/decision-table/create`, `/decision-table/edit/<key>` | |
| Forms to fill in (client) | `/form`, then `/form/<formId>` | |
| Tasks (reviewer) | `/task`, `/task/<taskId>` | |
| Submissions | `/submissions` | |
| Admin | `/admin` | |
| Bundles (premium) | `/bundleflow` to design, `/bundle` to submit | |

The side menu starts **collapsed**, with its labels hidden, so click menu items
by test id: `sidenav-forms`, `sidenav-subflows`, `sidenav-decision-tables`,
`sidenav-bundles`, `sidenav-metrics`, `sidenav-dashboards`,
`sidenav-submissions`, `accordion-header-task`, `accordion-header-submit`,
`accordion-header-manage`. Sign out is `sign-out-button`.

In 7.3.0 the section is "Design" not "Build", the item is "Forms & Flows", and
a form is created from a pop-up on the list, not at `/formflow/create`.

## The form designer

The builder is a palette on the left and the form on the right. Components are
added by dragging; there is no "add" button.

```
# A text field can be added to a new form
Environment: qa

Log in as someone who can design forms
Go to /formflow/create
Wait for header-form-title-input
Type "QA Copilot - text field test" into header-form-title-input
Drag "Text Field" onto css=.formio-builder-form
Wait for css=.formio-dialog.component-settings
Type "Applicant name" into css=.formio-dialog input[name="data[label]"]
Click css=.formio-dialog button[ref="saveButton"] >> nth=0
Check I can see css=.formio-builder-form .formio-component-textfield
Click save-form-layout
Check the page shows "Form and workflow created successfully"
```

What each part is doing, and what to change:

- **The palette.** Groups are Basic, Advanced, Layout, Data, Premium; only one
  is open at a time. Open another by clicking its heading
  (`Click css=button[data-target="#group-advanced"]`) before dragging from it.
  If a name appears twice or is not found, use the exact entry:
  `Drag css=span.formcomponent[data-type="textfield"] onto …`. Common
  `data-type` values: `textfield`, `textarea`, `number`, `checkbox`,
  `selectboxes`, `select`, `radio`, `button`, `email`, `phoneNumber`,
  `datetime`, `day`, `signature`, `file`, `panel`, `columns`, `table`,
  `container`, `datagrid`, `editgrid`. There is a search box:
  `css=input.builder-sidebar_search`.
- **The drop zone** is `css=.formio-builder-form`. On an empty form it shows
  "Drag and Drop a form component". To drop **into a layout component**, aim at
  its own zone: `css=.formio-builder-panel`, `css=.formio-builder-columns`.
- **After every drop a settings dialog opens** and the component is not on the
  form until it is saved. Its tabs are Display, Data, Validation, API,
  Conditional, Logic, Layout. Save, Cancel and Remove are
  `button[ref="saveButton"]`, `[ref="cancelButton"]`, `[ref="removeButton"]` —
  they appear **twice** when the preview pane is showing, hence `>> nth=0`.
  Forgetting this dialog is the commonest failure: the next drag lands on the
  dialog instead of the form.
- **Editing or removing a component that is already there.** Its toolbar only
  exists while the pointer is over it, so click the component first, then the
  toolbar button: `css=.builder-component [ref="editComponent"]`,
  `[ref="removeComponent"]`, `[ref="copyComponent"]`, `[ref="moveComponent"]`.
  With several components, pick one by key:
  `css=.formio-component-applicantName [ref="editComponent"]`.
  Removing asks "Are you sure you want to remove this component?" in a browser
  dialog; QA Copilot accepts browser dialogs and notes it in the step.
- **Reordering** is a drag of the component itself:
  `Drag css=.formio-component-email onto css=.formio-component-applicantName`.

Buttons on the form page (8.x): Save `save-form-layout`, Publish / Unpublish
`handle-publish-testid`, Discard `discard-button-testid`; tabs `tab-form`,
`tab-bpmn`, `tab-actions`; under Form: `form-builder-button`,
`form-settings-button`, `form-history-button`, `form-preview-button`.

- **Publish** asks to confirm: `Click "Publish This Form"`. It is disabled
  while the title is empty or still "Untitled Form". The confirmation is a
  short-lived banner reading `Published …`, gone in about three seconds —
  check the status pill reads "Published" instead of chasing the banner.
- **A published form is locked.** Unpublish before editing
  (`Click "Unpublish This Form & Flow"`). Saving a form that was published
  before asks "Save a new version" — choose `Create a new version` or `Update
  current version`.
- **Preview opens a new tab**: `Click form-preview-button`, then
  `Switch to the new tab`, check, then `Go back to the previous tab`.
- **Leaving with unsaved changes** raises "You Have Unsaved Changes" — a test
  that navigates away without saving must click `Discard Changes and Continue`.

Messages to check for: "Form updated successfully", "Form and workflow created
successfully", "Form deleted successfully".

## Filling in a form (client) — form.io controls

Every component is wrapped in `.formio-component-<key>`, where `<key>` is the
component's API key (from its API tab). That is the reliable way to address one.

| Control | How to drive it |
|---|---|
| Text, number, email | `Type "Asha" into css=.formio-component-firstName input` — or by its label if it is unique |
| **Select** | Not a real dropdown, so `Select … from …` fails. `Click css=.formio-component-country .choices`, then `Click css=.choices__list--dropdown .choices__item--choice:has-text("India")`. Options loaded from a URL arrive late: wait for the option first. |
| Checkbox, radio | `Click css=.formio-component-agree input` or tick it by label |
| Date / time | The visible box is a stand-in for a hidden one. Type into `css=.formio-component-dob input:not([type=hidden])` if typing is allowed, otherwise click a day: `css=.flatpickr-calendar.open .flatpickr-day:has-text("15")` |
| Signature | Drag across the pad: `Drag css=.signature-pad-canvas onto css=.signature-pad-canvas at 200, 60` |
| File upload | Not supported by QA Copilot yet — say so; do not fake it |
| Wizard (multi-page) | `Click css=.btn-wizard-nav-next`, `.btn-wizard-nav-previous`, `.btn-wizard-nav-submit` |
| Submit | The submit button is itself a component: `Click css=.formio-component-submit button` |

The client path is `/form` → **Select** on the form's row → the form. A
successful submission shows "Submission Saved" and returns to `/form`; a draft
shows "Submission saved to draft.".

A good end-to-end test follows one form through three people: a designer
publishes it, a client submits it, a reviewer finds the task at `/task`. Write
that as **three tests in one file**, one sign-in each — it reads better and a
failure says whose part broke.

## The BPMN editor (subflows, and the BPMN tab of a form)

The diagram is drawn in SVG; shapes have no text to click, so this is where
`css=` earns its place.

- **Canvas** `bpmneditor-canvas`. **Palette** entries:
  `css=.djs-palette .entry[data-action="create.task"]` — also
  `create.start-event`, `create.end-event`, `create.intermediate-event`,
  `create.exclusive-gateway`, `create.subprocess-expanded`,
  `create.participant-expanded`, `create.data-object`, `create.data-store`.
- **A shape on the canvas**: `css=g.djs-element[data-element-id="Activity_1"]`.
  Ids are generated, so when you do not know one, take the newest:
  `css=g.djs-shape >> nth=-1`.

Two ways to build, in order of reliability:

**1. Extend from an existing shape (clicks only — use this for a flow).** Click
a shape; a small pad appears beside it. Its entries add the next shape *and*
the arrow to it:

```
Click css=g.djs-shape[data-element-id^="StartEvent"]
Click css=.djs-context-pad .entry[data-action="append.append-task"]
Click css=.djs-context-pad .entry[data-action="append.end-event"]
```

Other pad entries: `append.gateway`, `append.intermediate-event`, `replace`
(change the type — pick "User task" from the list that opens), `connect`,
`delete`.

**2. Drag from the palette (to place a shape somewhere particular).**

```
Drag css=.djs-palette .entry[data-action="create.task"] onto bpmneditor-canvas at 420, 220
```

Keep the drop well to the right of the palette, which sits over the canvas's
left edge, and give each shape its own spot or they stack. If the drag leaves
nothing behind, click the palette entry and build onward with the pad instead.

- **Naming a shape**: select it, then
  `Type "Review application" into css=#bio-properties-panel-name`. The panel's
  sections start closed; open one by its heading
  (`css=.bio-properties-panel-group-header`). A user task's form is under
  "Forms".
- **Check the diagram**, not just the absence of an error:
  `Check I can see css=g.djs-shape >> nth=2`, or the name you typed with
  `Check the page shows "Review application"`.
- **Buttons**: Save `save-bpmn-layout` (in a form's BPMN tab: `save-flow-layout`),
  Publish `handle-publish-testid`, then `Click "Publish This BPMN"`. There is
  **no Deploy button — publishing is deploying.** A diagram with errors raises
  an "Error(s)" box; dismiss with `error-dismiss-button` and report what it
  said: that is a finding, not a flaky test.
- Messages: "BPMN <name> has been created successfully", "… updated
  successfully", "Process updated successfully".

Decision tables use the same page with `save-dmn-layout`.

## Bundles (premium)

A bundle is several single-page forms presented as one, with rules deciding
which forms appear, and one workflow for the lot. It exists only in the
enterprise edition, and only for accounts with the bundle permission: if
`sidenav-bundles` is not in the menu, the install does not have it — say so
instead of failing a test.

The steps, per the product documentation: **New Bundle** → name and description
→ **Add Forms** and pick them → **drag to reorder** → add conditions →
**Save & Preview** → **Next** (the flow) → **Next** (review, permissions,
publish) → **Save**. Wizard forms cannot be bundled. In review and analysis
screens the forms are stepped through with `bundle-next-form` and
`bundle-previous-form`.

The bundle screens are not in the public source, so the wording above is
unconfirmed. Before writing a bundle test, run a look-around test — sign in,
open `/bundleflow`, stop — and read the report's screenshots and the page
listing for the real names. Then write the test, and save what you learned as a
project skill.

## What to test, when asked to "test formsflow"

Offer this list rather than guessing which they mean:

1. **Smoke** — each role signs in and reaches its landing page; a designer
   sees no Tasks, a client sees no Build.
2. **Designer** — create a form, drag in three components, save, publish.
3. **Round trip** — publish, submit as a client, find the task as a reviewer.
4. **BPMN** — open a subflow, add a task, name it, save.
5. **Permissions** — a client opening `/formflow` is refused.
6. **Bundles** — only if the install has them.

## When a formsflow step fails

| What the report says | Usual cause |
|---|---|
| Could not find a palette entry | Its group is closed — open the group first |
| The drag passed but the component is not there | The settings dialog was not saved, or was still open from the previous drop |
| Click did nothing / timed out | The loading overlay was up — wait for the target first |
| `Select … from …` failed | It is a Choices dropdown — click it, then click the option |
| Test id not found | Some pages use `data-test-id` (with a hyphen) — write `css=[data-test-id="…"]` |
| Save is refused | The form is published — unpublish first |
| Everything fails after "Preview" | It opened a new tab — switch to it |
| Sign-in fails | The account or its group, not the feature — check before filing a defect |

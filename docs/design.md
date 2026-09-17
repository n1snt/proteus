# Product design

Status: implemented screen design. The entry screen and schema workspace have
been visually reviewed. UI review is manual; there is no automated browser suite.
See [requirements](requirements.md) and [architecture](architecture.md).

Screenshots: [entry screen](assets/landing.png) and [schema review](assets/workspace.png).

## Identity

Proteus is a precise, calm engineering tool. Use the name's link to changing form
through a small geometric mark, not a large mythology illustration.

Start with one light theme:

- Light neutral workspace, dark text, and subtle borders.
- One muted teal accent for primary actions and selection.
- A readable sans-serif face for UI text and monospace for identifiers and SQL.
- Compact controls and aligned table rows, with room around important decisions.
- Added, removed, changed, and conflicting states use labels and icons as well as
  color. Verify contrast before finalizing the actual colors.
- Small transitions only when they explain an interaction or state change.

Keep typography, color, spacing, and focus styles in shared tokens. Start with
system fonts and choose final font assets during the first visual review. Avoid
decorative dashboards, stock gradients, and cards that add no useful structure.

## Vocabulary

| Term | Meaning shown to the user |
| --- | --- |
| Main | The connected database that receives reviewed merges |
| Branch | An isolated schema-only database for a set of changes |
| Draft | Saved edits that have not yet changed the branch database |
| Revision | A successfully applied schema state in history |
| Merge | Bring a branch's changes into main after review |
| Migration | The database operations used to apply a revision |

Use actual branch names in conflict choices. Do not rely on 'ours' and 'theirs'.
Use 'Apply and commit' for a branch draft and 'Apply merge' for a merge plan.

## 1. Start or connect

The entry screen has two clear paths:

- 'Try sample database': create an isolated financial-operations workspace.
- 'Connect PostgreSQL': enter a URL or separate connection fields in a self-hosted
  deployment. The public demo links to self-hosted setup for connecting a user's
  own database.

Connection steps: enter details, test access, review support, choose the schema,
and import the baseline. Explain that 'localhost' inside the application container
is that container, not the user's machine. Offer clear connection guidance for
databases in the Compose network, on the host machine, and on remote servers.

Distinguish unreachable host, bad credentials, TLS problems, missing privileges,
unsupported PostgreSQL version, and unsupported schema objects. Preserve useful
form input after failure, but never display or log the password in error details.

## 2. Branch workspace

    Header: project / database / branch       Status       Compare
    --------------------------------------------------------------
    Object browser | Selected table structure | Draft changes
                   | Columns                  | Change summary
                   | Constraints              | SQL preview
                   | Indexes                  | Commit message
                   |                          | Apply and commit

The branch picker shows the current branch and main. Branch creation explains:
'Copies the schema. Existing rows are not copied.'

Use structured actions for adding, editing, renaming, and removing objects. A
rename is its own operation. Type choices and expression inputs follow the
support matrix. Show dependent objects before a destructive change is submitted.

Draft edits update the projected schema, not the database. Label this clearly and
show save status. Users can undo draft changes and inspect the full SQL plan.
Disable duplicate application while the submitted draft is running.

Main is read-only in the draft editor. Its primary action is 'Create branch'.
For a merged source branch, show its final state and offer a new branch from main.

## 3. Compare and merge

    Source branch -> main                Pinned revisions
    --------------------------------------------------------------
    Changed objects | Semantic diff / conflict | Result and impact
                    | Base                    | Lock / scan / rewrite
                    | Source change           | Data-loss effects
                    | Main change             | Generated SQL
                    | Resolution              | Apply merge

Show a compact summary, for example: '7 changes across 3 tables. 1 conflict.'
Group differences by table, with filters for additions, changes, removals, and
conflicts. Keep SQL available as a secondary view rather than the only explanation.

For each conflict, show the affected definition, why it conflicts, and the result
of the selected resolution. Preserve unrelated compatible changes. Disable apply
until all conflicts and structural errors are resolved.

The final review names the target database, source revision, and main revision.
Show destructive effects inline. A stale plan explains which revision changed and
offers a fresh comparison. Do not present it as a generic server error.

## 4. Execution and history

Show the current step, elapsed time, completed steps, and useful database details.
Distinguish waiting for a lock, applying DDL, validating data, and verifying the
result. Use a progress percentage only when based on real database information.

On success, show the new revision and verified changes. On failure, distinguish:

- No changes committed by the failed transactional phase.
- Earlier phases completed and later work remains.
- Outcome is being checked after a connection loss or restart.
- Manual attention is needed before the target can accept more work.

History shows messages, timestamps, short revision IDs, merge parents, and outcomes.
Use a readable list first; a complex graph is not needed for the core workflow.
Refreshing or reopening a job page must preserve the server's execution state.

## Demo story

Use customers, invoices, invoice items, and payments. Demonstrate a column rename
on one branch and a nullability change on another branch. Merge one into main,
then show the other combining correctly using the shared column identity.

Also provide a repeatable conflicting-rename scenario. The demonstration must
use the real merge engine and real databases, not precomputed success screens.
Keep the 5 GB benchmark environment separate from disposable visitor workspaces.

## Review checklist

- All interactive controls have useful labels and visible keyboard focus.
- Forms show field errors near the input and preserve unrelated entries.
- Status is understandable without color.
- Long names and SQL do not break the page layout.
- Narrow layouts stack panels without hiding the target or primary action.
- Loading, empty, expired-session, error, and partial-execution states are designed.
- Destructive effects and transaction limits are explained before application.
- No clickable controls lead to unimplemented or fake behavior.
- Review real screens at desktop and narrow viewport sizes during each milestone.

# Working rules

These rules apply to all work in this repository.

## Project goal

Build a web app for PostgreSQL schema branching, diffs, and merges. Apply changes
to real databases and test behavior with tables holding roughly 5 GB of data.
Favor a complete, clear workflow over a long feature list.

Treat self-hosted Docker use as the primary product experience. The hosted demo
uses the same app with isolated sample databases. Core workflows must not depend
on the demo service or an E2E Networks account.

Use Python for the backend. Read 'decisions.md' before changing the design.
Proposed decisions are not settled requirements. Keep the code easy for its owner
to explain, debug, and change in an interview.

Read 'docs/requirements.md' for scope, 'docs/architecture.md' for the approved
design, 'docs/compatibility.md' for database limits, and 'docs/design.md' for UI
behavior. These are implementation specs, not proof that a feature is built.

## Writing

- Use plain English, short sentences, and familiar words.
- Avoid complex words when a simpler word says the same thing.
- Use technical terms when needed, and explain them on first use.
- Do not use em dashes. Use a period, comma, colon, or ordinary hyphen instead.
- Use single quotes around inline names, paths, and commands. Do not use Markdown
  backticks. Use indented code blocks or tilde fences for examples.
- Apply these prose rules to docs, comments, UI text, and commit messages.
- Preserve valid language syntax. Required quotes in JSON, SQL, or source code are
  not subject to the prose quote rule. Do not rewrite third-party or generated
  files just to change punctuation.
- Be direct. Avoid hype, filler, and claims that the code or measurements do not
  support.
- Clearly label planned, built, and verified behavior. Never invent test results,
  benchmark numbers, or a working setup command.

## Code and scope

- Prefer small functions, clear names, type hints, and explicit data structures.
- Keep schema, diff, and merge logic separate from HTTP and database access.
- Keep SQL execution, connection ownership, and transaction boundaries clear.
- Use bound parameters for SQL values and proper identifier composition for names.
- Add abstractions only when they solve a current problem.
- Explain the reason for surprising code. Do not add comments that repeat it.
- Proteus owns tracked schema changes after import. Do not add external schema
  change detection or reconciliation without an explicit scope decision.
- Continue to handle concurrent data writes, failed migrations, and interrupted
  execution. Those are part of applying our own changes correctly.
- Keep each change focused. Do not overwrite unrelated work or commit secrets,
  database contents, local settings, or build output.

## Testing strategy

Write enough tests to protect important behavior. Keep the suite small, useful,
and easy to maintain. Do not chase a coverage percentage or a test-count target.
Before adding a test, name the real bug it would catch and check whether an
existing test already covers that behavior.

### Where tests add value

- Use unit tests for schema rules, rename handling, merge conflicts, and operation
  ordering. Assert expected results rather than copying the algorithm into a test.
- Use real PostgreSQL integration tests for generated DDL, constraints, locks,
  transaction outcomes, and restart recovery. Database mocks cannot prove these.
- Keep browser tests to the main user journeys, including a clean merge and a
  resolved conflict. Add focused UI tests only for meaningful interaction logic.
- Run the required 5 GB benchmark separately from the normal fast suite. Document
  the setup and measurements when the benchmark exists.

### Keep tests lean

- Test at the lowest layer that proves the behavior. Do not repeat every case at
  unit, API, and browser levels unless each test catches a different kind of bug.
- Prioritize data loss, partial execution, stale plans, and duplicate application.
  Cover distinct failure modes, not every possible combination of inputs.
- Skip tests for trivial getters, framework behavior, static copy, and cosmetic
  changes. Use document review or visual checks where those give better evidence.
- Avoid brittle full-page snapshots, assertions on private helpers, and mock-call
  checks that merely mirror the implementation.
- Keep fixtures small and deterministic. Reuse setup without building a large
  custom test framework. Add a regression case when it protects a real bug that
  existing tests miss.
- Do not delete, weaken, or overwrite useful tests just to make a change pass.
  Update expectations only when intended behavior changes.

Run the relevant tests and required checks for each change. Once they pass, do
not broaden or repeat testing without a new change, failure, unresolved concern,
or required release check. Docs-only edits need content, link, and style review,
not application tests. Record actual commands and outcomes; report blocked checks
instead of claiming they passed. Put working test instructions in 'docs/testing.md'.

## Documentation strategy

Keep most documentation in 'docs/'. Use lowercase, hyphen-separated file names.
Keep these three entry points at the repository root:

- 'README.md': short overview, current status, working quickstart when available,
  and links to deeper docs.
- 'AGENTS.md': writing, coding, documentation, and commit rules.
- 'decisions.md': the history of meaningful choices and their tradeoffs.

Use these paths as each topic needs documentation:

| Path | Contents |
| --- | --- |
| 'docs/requirements.md' | User workflows, scope, and acceptance criteria tied to the assignment |
| 'docs/architecture.md' | Components, data model, request flow, merge rules, and execution states |
| 'docs/design.md' | Screen layouts, visual choices, interactions, and UI states |
| 'docs/compatibility.md' | Supported PostgreSQL versions, objects, operations, and limits |
| 'docs/development.md' | Local setup, configuration, and common development tasks |
| 'docs/testing.md' | Test commands, fixtures, failure cases, and how to reproduce them |
| 'docs/deployment.md' | Docker setup, E2E Networks deployment, storage, and recovery steps |
| 'docs/benchmarks.md' | Workloads, hardware, commands, measured results, and limits |

Store screenshots and diagrams in 'docs/assets/' and link to them from the relevant
page. Keep runnable scripts, test fixtures, and deployment files with their code;
link to them instead of copying their contents into docs.

Create a document when it has useful content. Do not add empty placeholders or
separate pages for every small change. Split a page only when that improves
navigation. Add 'docs/README.md' as an index if the collection becomes hard to
browse, and link the main guides from the root README.

Give each topic one main home. Describe the current design in 'architecture.md'
and the reasons for past choices in 'decisions.md'. Link between them rather than
repeating the same explanation. Clearly label proposed designs until accepted.

Update affected docs alongside code in the same change. Check paths, relative
links, examples, and commands. Record only checks and measurements actually run.
Remove or correct stale instructions when behavior changes.

### Decision log

Keep 'decisions.md' as a record of choices, not a changelog. Update it in the same
commit as a meaningful design or scope change. Each entry should include:

- A stable ID, title, date, and status.
- The choice and the problem it addresses.
- Alternatives that were seriously considered.
- Reasons, accepted tradeoffs, and deliberate cuts.
- Evidence, or a clear statement of what still needs to be tested.

Use 'Proposed', 'Accepted', or 'Superseded' as the status. Preserve old reasoning
when a decision changes and link to its replacement. Routine fixes and formatting
changes do not need new decision entries.

## Commits

Commit only when the user requests a commit or grants an ongoing commit workflow.
A request for one commit does not grant permission for later commits or pushes.

The owner has authorized local commits at verified implementation milestones for
this build. Keep commits focused and follow the checks below. Remote pushes and
deployment will follow when the owner provides the repository and SSH access.

Before committing:

1. Inspect 'git status', the full diff, and 'git log --oneline -10'. For a new
   repository, confirm that no history exists yet.
2. Run the relevant checks and review docs for accuracy and writing style.
3. Stage only the intended files using explicit paths.
4. Review the staged diff and check it for whitespace errors and secrets.
5. Commit one logical, working change, including related tests and docs.
6. Check the resulting commit and working tree.

Write commit subjects in the form 'type(scope): short action'. Use lowercase types
and scopes, an imperative verb, and no final period. Aim for at most 72 characters.

Types: 'feat', 'fix', 'refactor', 'test', 'docs', 'build', 'ci', and 'chore'. Choose
a specific scope such as 'schema', 'merge', 'executor', 'web', or 'repo'.

Examples:

- 'docs(repo): add project overview and working rules'
- 'feat(merge): detect conflicting column renames'
- 'fix(executor): recover a committed migration after restart'
- 'test(postgres): cover invalid type conversions'

Add a body when the reason or tradeoff is not clear from the subject. Explain why,
note relevant verification, and reference a decision ID when useful. Avoid vague
messages such as 'update files', 'misc fixes', or 'final changes'.

Keep history honest. Do not manufacture earlier work, rewrite published history,
amend commits, bypass hooks, or push without permission. If a hook fails, fix the
issue, repeat the needed checks, and make a new commit attempt.

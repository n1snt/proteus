# Working rules

These rules apply to all work in this repository.

## Project goal

Build a web app for PostgreSQL schema branching, diffs, and merges. Apply changes
to real databases and test behavior with tables holding roughly 5 GB of data.
Favor a complete, clear workflow over a long feature list.

Use Python for the backend. Read 'decisions.md' before changing the design.
Proposed decisions are not settled requirements. Keep the code easy for its owner
to explain, debug, and change in an interview.

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
- Run checks suited to the change. Test real failure cases rather than chasing a
  coverage number. Docs-only edits need review, not application tests.
- Keep each change focused. Do not overwrite unrelated work or commit secrets,
  database contents, local settings, or build output.

## Documentation

Keep 'README.md' short: purpose, current status, working setup instructions when
available, and links to deeper notes. Put detailed design in supporting docs.

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

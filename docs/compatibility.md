# PostgreSQL compatibility

Status: implemented support boundary. Focused PostgreSQL 17 tests and the large
table benchmark verify representative operations and failure cases, rather than
every possible combination. Mark scope changes in the [decision log](../decisions.md).

## Database boundary

- PostgreSQL 17 for both connected targets and sandbox databases.
- One selected application schema per project.
- Ordinary tables and supported dependencies within that schema.
- Main credentials must own the tracked objects or have the required owner-role
  access, and must be able to create and maintain the '_proteus' tracking schema.
- Database creation privileges are required only on the managed sandbox.
- Names are composed as PostgreSQL identifiers, including quoted names. They are
  never treated as SQL value parameters or inserted through string concatenation.

Reject import when an unsupported object or dependency affects the tracked schema.
Show its name and the reason. Do not silently import a partial model. Unrelated
schemas may exist, but supported objects must not depend on their user objects.

## Initial object support

| Area | Initial support |
| --- | --- |
| Tables | Create and drop ordinary tables |
| Columns | Add, drop, rename, change supported types, defaults, and nullability |
| Scalar types | Boolean; smallint, integer, bigint; real, double precision; numeric with precision/scale; text and varchar with optional length; UUID; date; timestamp with or without time zone; JSONB; bytea |
| Identity | Standard identity columns with default sequence settings; custom sequence options are rejected |
| Defaults | Typed literals, null, and current timestamp; additional forms only after explicit support and tests |
| Primary keys | Single-column and composite keys |
| Unique constraints | Single-column and composite, with default null handling |
| Foreign keys | Non-deferrable references within the tracked schema, with default match behavior and NO ACTION, RESTRICT, CASCADE, SET NULL, or SET DEFAULT actions |
| Check constraints | A structured column-to-literal comparison or null check; expand the expression grammar only with tests |
| Indexes | Ordinary ascending single-column and composite B-tree indexes with default null ordering, including ordinary unique indexes |

Use a documented restricted expression grammar for defaults and checks. Import
must recognize only supported forms using a proper parsing approach. Do not guess
from regular expressions or accept arbitrary SQL through the editor. Report other
forms as unsupported until they can be modeled and round-tripped correctly.

Preserve meaningful index key order. Non-default sort and null ordering are
reported as unsupported. Constraint-owned
indexes belong to the constraint model. User-defined collations, operator classes,
special storage settings, and unusual index or constraint options require explicit
support rather than silently falling back to defaults.

System-created objects that implement supported features, such as foreign-key
triggers and identity sequences, belong to those features. Distinguish them from
unsupported user-created triggers and standalone sequences during catalog checks.

## Type conversion boundary

Importing a type does not mean every conversion to or from that type is supported.
The initial conversion matrix targets:

- Widening smallint to integer or bigint, and integer to bigint.
- Increasing varchar length and converting varchar to text.
- Changing numeric precision or scale, subject to PostgreSQL validation and any
  rounding effects disclosed in the plan.
- Integer to text, and text to integer using an explicit cast, with invalid-row
  failures covered by integration tests.

Before release, each supported conversion needs a test for values, constraints,
defaults, and dependencies that could affect it. Unsupported conversions are
rejected in the editor and API. Do not classify all widening conversions as
rewrite-free; inspect the exact PostgreSQL operation.

## Large-table execution strategies

| Change | Planned strategy and limits |
| --- | --- |
| Rename or add nullable column | Metadata-oriented DDL with bounded lock waiting; lock acquisition can still block |
| Add suitable constant default | Use PostgreSQL's fast-default behavior only for supported cases |
| Create index | Concurrent build on populated main tables, outside a transaction block |
| Add unique constraint | Build an eligible unique index concurrently, then attach the constraint |
| Add primary key | Establish non-null columns and an eligible unique index before attachment |
| Add foreign key or check | Add as not valid, then validate in a separate phase |
| Set non-null | Validate a supporting check first, then set non-null without repeating a full scan under the final lock |
| Change type | Run a tested conversion; disclose scans, rewrites, dependent index work, and blocking effects |
| Drop table or column | Show data loss and dependency effects; no promise of data restoration |

'Not valid' does not mean disabled: new writes must satisfy the constraint while
older rows await validation. Preliminary row checks alone cannot prove safety
under concurrent writes. The database must enforce the final rule.

Some migrations contain several committed phases. A failure can leave a temporary
check or index behind. Track these as operation-owned objects and include them in
recovery. Do not describe a multi-phase migration as fully atomic.

## Outside the first release

- Partitioned tables, table inheritance, foreign tables, and materialized views.
- Views, triggers, rules, stored procedures, and user-defined functions.
- Arrays, domains, enums, ranges, and extension-defined types.
- Standalone sequences and serial-style defaults; use identity columns in fixtures.
- Generated expression columns and arbitrary SQL expressions.
- Partial, expression, covering, and non-B-tree indexes.
- Cross-schema foreign keys, exclusion constraints, deferrable constraints, and
  non-default foreign-key match modes.
- Row-level security policies, grants, ownership changes, and custom tablespaces
  as versioned objects. Active unsupported policies must be reported on import.
- Changes made by other migration tools after baseline import.

Keep baseline permission checks separate from versioning grants or ownership.
Check the catalog for unsupported features before accepting the baseline or edits.

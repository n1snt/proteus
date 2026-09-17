"""Pure PostgreSQL DDL plan generation for supported snapshots."""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg import sql

from .schema import rewrite_check_expression, validate_snapshot


def _identifier(value: str) -> sql.Identifier:
    return sql.Identifier(value)


def _qualified(schema_name: str, name: str) -> sql.Composed:
    return sql.SQL('{}.{}').format(_identifier(schema_name), _identifier(name))


def _render(statement: sql.Composable) -> str:
    return statement.as_string(None)


def _by_id(values: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {value['id']: value for value in values}


def _columns(table: dict[str, Any], ids: list[str]) -> list[str]:
    by_id = _by_id(table['columns'])
    return [by_id[column_id]['name'] for column_id in ids]


def _column_definition(column: dict[str, Any]) -> sql.Composed:
    bits: list[sql.Composable] = [_identifier(column['name']), sql.SQL(column['data_type'])]
    if column['identity']:
        generation = 'ALWAYS' if column['identity'] == 'always' else 'BY DEFAULT'
        bits.append(sql.SQL(f'GENERATED {generation} AS IDENTITY'))
    if column['default'] is not None:
        bits.append(sql.SQL('DEFAULT ') + sql.SQL(column['default']))
    if not column['nullable']:
        bits.append(sql.SQL('NOT NULL'))
    return sql.SQL(' ').join(bits)


def _step_id(operation: dict[str, Any], number: int) -> str:
    encoded = repr(sorted(operation.items())).encode()
    return f'step_{number}_{hashlib.sha256(encoded).hexdigest()[:12]}'


class _Plan:
    def __init__(self, schema_name: str, online: bool) -> None:
        self.schema_name = schema_name
        self.online = online
        self.steps: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    def add(
        self,
        statement: sql.Composable,
        description: str,
        transactional: bool,
        impact: str,
        operation: dict[str, Any],
    ) -> None:
        self.steps.append(
            {
                'id': _step_id(operation, len(self.steps) + 1),
                'sql': _render(statement),
                'description': description,
                'transactional': transactional,
                'impact': impact,
                'operation': operation,
            }
        )

    def warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def _type_conversion_allowed(before: str, after: str) -> bool:
    if before == after:
        return True
    chain = {'smallint': {'integer', 'bigint'}, 'integer': {'bigint', 'text'}, 'text': {'integer'}}
    if after in chain.get(before, set()):
        return True
    if before.startswith('varchar') and after == 'text':
        return True
    if before.startswith('varchar') and after.startswith('varchar'):
        old_length = int(before[8:-1]) if before != 'varchar' else None
        new_length = int(after[8:-1]) if after != 'varchar' else None
        return old_length is not None and (new_length is None or new_length >= old_length)
    return before.startswith('numeric') and after.startswith('numeric')


def _constraint_statement(
    schema_name: str,
    table: dict[str, Any],
    item: dict[str, Any],
    tables: dict[str, dict[str, Any]],
    not_valid: bool = False,
) -> sql.Composed:
    table_name = _qualified(schema_name, table['name'])
    name = _identifier(item['name'])
    columns = sql.SQL(', ').join(_identifier(value) for value in _columns(table, item['columns']))
    if item['kind'] == 'primary_key':
        definition = sql.SQL('PRIMARY KEY ({})').format(columns)
    elif item['kind'] == 'unique':
        definition = sql.SQL('UNIQUE ({})').format(columns)
    elif item['kind'] == 'check':
        definition = sql.SQL('CHECK ({})').format(sql.SQL(item['expression']))
    else:
        reference = tables[item['reference_table']]
        ref_columns = sql.SQL(', ').join(
            _identifier(value) for value in _columns(reference, item['reference_columns'])
        )
        actions = []
        if item['on_delete']:
            actions.append(sql.SQL('ON DELETE ' + item['on_delete']))
        if item['on_update']:
            actions.append(sql.SQL('ON UPDATE ' + item['on_update']))
        definition = sql.SQL('FOREIGN KEY ({}) REFERENCES {} ({})').format(
            columns, _qualified(schema_name, reference['name']), ref_columns
        )
        if actions:
            definition += sql.SQL(' ') + sql.SQL(' ').join(actions)
    suffix = sql.SQL(' NOT VALID') if not_valid else sql.SQL('')
    return sql.SQL('ALTER TABLE {} ADD CONSTRAINT {} {}{}').format(
        table_name, name, definition, suffix
    )


def _add_constraint(
    plan: _Plan, table: dict[str, Any], item: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> None:
    metadata = {
        'kind': 'add_constraint',
        'schema': plan.schema_name,
        'table': table['name'],
        'name': item['name'],
        'definition': item,
    }
    if item['kind'] == 'primary_key':
        by_id = _by_id(table['columns'])
        nullable = [
            by_id[column_id]['name']
            for column_id in item['columns']
            if by_id[column_id]['nullable']
        ]
        if nullable:
            raise ValueError(
                f'primary key {item["name"]} requires non-null columns: {", ".join(nullable)}'
            )
    if item['kind'] in {'foreign_key', 'check'} and plan.online:
        plan.add(
            _constraint_statement(plan.schema_name, table, item, tables, not_valid=True),
            f'Add {item["kind"].replace("_", " ")} {item["name"]} without validating existing rows',
            True,
            'metadata',
            metadata,
        )
        plan.add(
            sql.SQL('ALTER TABLE {} VALIDATE CONSTRAINT {}').format(
                _qualified(plan.schema_name, table['name']), _identifier(item['name'])
            ),
            f'Validate constraint {item["name"]}',
            True,
            'scan',
            {
                'kind': 'validate_constraint',
                'schema': plan.schema_name,
                'table': table['name'],
                'name': item['name'],
            },
        )
        return
    if item['kind'] in {'primary_key', 'unique'} and plan.online:
        index_name = f'{item["name"]}__proteus_idx'
        if len(index_name.encode()) > 63:
            index_name = f'proteus_{hashlib.sha256(item["id"].encode()).hexdigest()[:20]}_idx'
        columns = _columns(table, item['columns'])
        plan.add(
            sql.SQL('CREATE UNIQUE INDEX CONCURRENTLY {} ON {} ({})').format(
                _identifier(index_name),
                _qualified(plan.schema_name, table['name']),
                sql.SQL(', ').join(_identifier(value) for value in columns),
            ),
            f'Build unique index for {item["name"]}',
            False,
            'index',
            {
                'kind': 'create_index',
                'schema': plan.schema_name,
                'name': index_name,
                'table': table['name'],
                'unique': True,
                'columns': columns,
            },
        )
        kind = 'PRIMARY KEY' if item['kind'] == 'primary_key' else 'UNIQUE'
        plan.add(
            sql.SQL('ALTER TABLE {} ADD CONSTRAINT {} {} USING INDEX {}').format(
                _qualified(plan.schema_name, table['name']),
                _identifier(item['name']),
                sql.SQL(kind),
                _identifier(index_name),
            ),
            f'Attach {item["kind"].replace("_", " ")} {item["name"]}',
            True,
            'metadata',
            metadata,
        )
        return
    plan.add(
        _constraint_statement(plan.schema_name, table, item, tables),
        f'Add {item["kind"].replace("_", " ")} {item["name"]}',
        True,
        'scan' if item['kind'] in {'foreign_key', 'check'} else 'metadata',
        metadata,
    )


def _add_index(plan: _Plan, table: dict[str, Any], item: dict[str, Any]) -> None:
    columns = _columns(table, item['columns'])
    concurrent = ' CONCURRENTLY' if plan.online else ''
    unique = 'UNIQUE ' if item['unique'] else ''
    plan.add(
        sql.SQL(f'CREATE {unique}INDEX{concurrent} {{}} ON {{}} ({{}})').format(
            _identifier(item['name']),
            _qualified(plan.schema_name, table['name']),
            sql.SQL(', ').join(_identifier(value) for value in columns),
        ),
        f'Create index {item["name"]}',
        not plan.online,
        'index',
        {
            'kind': 'create_index',
            'schema': plan.schema_name,
            'name': item['name'],
            'table': table['name'],
            'unique': item['unique'],
            'columns': columns,
        },
    )


def _drop_constraint(plan: _Plan, table_name: str, item: dict[str, Any]) -> None:
    plan.add(
        sql.SQL('ALTER TABLE {} DROP CONSTRAINT {}').format(
            _qualified(plan.schema_name, table_name), _identifier(item['name'])
        ),
        f'Drop constraint {item["name"]}',
        True,
        'destructive',
        {
            'kind': 'drop_constraint',
            'schema': plan.schema_name,
            'table': table_name,
            'name': item['name'],
            'definition': item,
        },
    )


def _drop_index(plan: _Plan, table: dict[str, Any], item: dict[str, Any]) -> None:
    concurrent = ' CONCURRENTLY' if plan.online else ''
    columns = _columns(table, item['columns'])
    plan.add(
        sql.SQL(f'DROP INDEX{concurrent} {{}}').format(_qualified(plan.schema_name, item['name'])),
        f'Drop index {item["name"]}',
        not plan.online,
        'index',
        {
            'kind': 'drop_index',
            'schema': plan.schema_name,
            'name': item['name'],
            'table': table['name'],
            'unique': item['unique'],
            'columns': columns,
        },
    )


def _temporary_check_name(column_id: str) -> str:
    return f'proteus_nn_{hashlib.sha256(column_id.encode()).hexdigest()[:16]}'


def _changed_column_ids(
    before_tables: dict[str, dict[str, Any]], after_tables: dict[str, dict[str, Any]]
) -> set[tuple[str, str]]:
    changed: set[tuple[str, str]] = set()
    for table_id, before_table in before_tables.items():
        after_table = after_tables.get(table_id)
        if after_table is None:
            changed.update((table_id, column['id']) for column in before_table['columns'])
            continue
        after_columns = _by_id(after_table['columns'])
        for column in before_table['columns']:
            current = after_columns.get(column['id'])
            if current is None or current['data_type'] != column['data_type']:
                changed.add((table_id, column['id']))
    return changed


def _constraint_depends_on_changed_column(
    table_id: str, item: dict[str, Any], changed_columns: set[tuple[str, str]]
) -> bool:
    if any((table_id, column_id) in changed_columns for column_id in item['columns']):
        return True
    return item['kind'] == 'foreign_key' and any(
        (item['reference_table'], column_id) in changed_columns
        for column_id in item['reference_columns']
    )


def _alter_column(
    plan: _Plan,
    before_table: dict[str, Any],
    after_table: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    table = _qualified(plan.schema_name, after_table['name'])
    old_name, name = before['name'], after['name']
    if old_name != name:
        plan.add(
            sql.SQL('ALTER TABLE {} RENAME COLUMN {} TO {}').format(
                table, _identifier(old_name), _identifier(name)
            ),
            f'Rename column {old_name} to {name}',
            True,
            'metadata',
            {
                'kind': 'rename_column',
                'schema': plan.schema_name,
                'table': after_table['name'],
                'name': old_name,
                'new_name': name,
                'definition': after,
            },
        )
    default_was_dropped = before['default'] is not None and (
        before['default'] != after['default'] or before['data_type'] != after['data_type']
    )
    if default_was_dropped:
        plan.add(
            sql.SQL('ALTER TABLE {} ALTER COLUMN {} DROP DEFAULT').format(table, _identifier(name)),
            f'Drop default from {name}',
            True,
            'metadata',
            {
                'kind': 'drop_default',
                'schema': plan.schema_name,
                'table': after_table['name'],
                'name': name,
            },
        )
    if before['data_type'] != after['data_type']:
        if not _type_conversion_allowed(before['data_type'], after['data_type']):
            raise ValueError(
                f'unsupported type conversion from {before["data_type"]} to {after["data_type"]} on {after_table["name"]}.{name}'
            )
        using = sql.SQL('')
        if (before['data_type'], after['data_type']) in {('integer', 'text'), ('text', 'integer')}:
            using = sql.SQL(' USING {}::{}').format(_identifier(name), sql.SQL(after['data_type']))
        plan.add(
            sql.SQL('ALTER TABLE {} ALTER COLUMN {} TYPE {}{}').format(
                table, _identifier(name), sql.SQL(after['data_type']), using
            ),
            f'Change type of {name} to {after["data_type"]}',
            True,
            'rewrite',
            {
                'kind': 'retype_column',
                'schema': plan.schema_name,
                'table': after_table['name'],
                'name': name,
                'before': before['data_type'],
                'after': after['data_type'],
                'definition': after,
            },
        )
        plan.warning(f'Changing {after_table["name"]}.{name} may scan or rewrite existing rows.')
    if before['identity'] != after['identity']:
        if before['identity'] is not None or after['identity'] is None:
            raise ValueError(
                f'changing or removing identity is not supported for {after_table["name"]}.{name}'
            )
        generation = 'ALWAYS' if after['identity'] == 'always' else 'BY DEFAULT'
        plan.add(
            sql.SQL(
                f'ALTER TABLE {{}} ALTER COLUMN {{}} ADD GENERATED {generation} AS IDENTITY'
            ).format(table, _identifier(name)),
            f'Add identity to {name}',
            True,
            'metadata',
            {
                'kind': 'add_identity',
                'schema': plan.schema_name,
                'table': after_table['name'],
                'name': name,
                'definition': after,
            },
        )
    if before['nullable'] != after['nullable']:
        if after['nullable']:
            plan.add(
                sql.SQL('ALTER TABLE {} ALTER COLUMN {} DROP NOT NULL').format(
                    table, _identifier(name)
                ),
                f'Allow null values in {name}',
                True,
                'metadata',
                {
                    'kind': 'drop_not_null',
                    'schema': plan.schema_name,
                    'table': after_table['name'],
                    'name': name,
                },
            )
        elif plan.online:
            check_name = _temporary_check_name(after['id'])
            plan.add(
                sql.SQL('ALTER TABLE {} ADD CONSTRAINT {} CHECK ({} IS NOT NULL) NOT VALID').format(
                    table, _identifier(check_name), _identifier(name)
                ),
                f'Add supporting null check for {name}',
                True,
                'metadata',
                {
                    'kind': 'supporting_not_null_check',
                    'schema': plan.schema_name,
                    'table': after_table['name'],
                    'name': check_name,
                    'column': name,
                },
            )
            plan.add(
                sql.SQL('ALTER TABLE {} VALIDATE CONSTRAINT {}').format(
                    table, _identifier(check_name)
                ),
                f'Validate null check for {name}',
                True,
                'scan',
                {
                    'kind': 'validate_constraint',
                    'schema': plan.schema_name,
                    'table': after_table['name'],
                    'name': check_name,
                },
            )
            plan.add(
                sql.SQL('ALTER TABLE {} ALTER COLUMN {} SET NOT NULL').format(
                    table, _identifier(name)
                ),
                f'Require values in {name}',
                True,
                'metadata',
                {
                    'kind': 'set_not_null',
                    'schema': plan.schema_name,
                    'table': after_table['name'],
                    'name': name,
                },
            )
            plan.add(
                sql.SQL('ALTER TABLE {} DROP CONSTRAINT {}').format(table, _identifier(check_name)),
                f'Remove supporting null check for {name}',
                True,
                'metadata',
                {
                    'kind': 'drop_supporting_not_null_check',
                    'schema': plan.schema_name,
                    'table': after_table['name'],
                    'name': check_name,
                    'column': name,
                },
            )
        else:
            plan.add(
                sql.SQL('ALTER TABLE {} ALTER COLUMN {} SET NOT NULL').format(
                    table, _identifier(name)
                ),
                f'Require values in {name}',
                True,
                'scan',
                {
                    'kind': 'set_not_null',
                    'schema': plan.schema_name,
                    'table': after_table['name'],
                    'name': name,
                },
            )
    if (before['default'] != after['default'] or default_was_dropped) and after[
        'default'
    ] is not None:
        plan.add(
            sql.SQL('ALTER TABLE {} ALTER COLUMN {} SET DEFAULT {}').format(
                table, _identifier(name), sql.SQL(after['default'])
            ),
            f'Set default for {name}',
            True,
            'metadata',
            {
                'kind': 'set_default',
                'schema': plan.schema_name,
                'table': after_table['name'],
                'name': name,
                'definition': after,
            },
        )


def build_plan(before: Any, after: Any, schema_name: str, online: bool = True) -> dict[str, Any]:
    """Build ordered DDL with no database access."""
    if (
        not isinstance(schema_name, str)
        or not schema_name.strip()
        or '\x00' in schema_name
        or len(schema_name.encode()) > 63
    ):
        raise ValueError('schema_name must be a PostgreSQL identifier')
    old, new = validate_snapshot(before), validate_snapshot(after)
    plan = _Plan(schema_name, online)
    old_tables, new_tables = _by_id(old['tables']), _by_id(new['tables'])
    # PostgreSQL itself rebinds checks on rename. Compare their definitions in the
    # final name space so a rename does not unnecessarily drop and rebuild them.
    for table_id in old_tables.keys() & new_tables.keys():
        names = {column['id']: column['name'] for column in new_tables[table_id]['columns']}
        renames = {
            column['name']: names[column['id']]
            for column in old_tables[table_id]['columns']
            if column['id'] in names
        }
        for constraint in old_tables[table_id]['constraints']:
            if constraint['kind'] == 'check':
                constraint['expression'] = rewrite_check_expression(
                    constraint['expression'], renames
                )
    changed_columns = _changed_column_ids(old_tables, new_tables)
    dropped_constraints: set[tuple[str, str]] = set()
    # Remove foreign keys and constraints before their columns or referenced tables.
    for foreign_keys in (True, False):
        for table_id in sorted(old_tables):
            old_table = old_tables[table_id]
            new_constraints = (
                _by_id(new_tables[table_id]['constraints']) if table_id in new_tables else {}
            )
            for item_id, item in sorted(_by_id(old_table['constraints']).items()):
                if (item['kind'] == 'foreign_key') != foreign_keys:
                    continue
                if (
                    item_id not in new_constraints
                    or item != new_constraints[item_id]
                    or _constraint_depends_on_changed_column(table_id, item, changed_columns)
                ):
                    _drop_constraint(plan, old_table['name'], item)
                    dropped_constraints.add((table_id, item_id))
    for table_id in sorted(old_tables.keys() & new_tables.keys()):
        old_indexes, new_indexes = (
            _by_id(old_tables[table_id]['indexes']),
            _by_id(new_tables[table_id]['indexes']),
        )
        for item_id, item in sorted(old_indexes.items()):
            if item_id not in new_indexes or item != new_indexes[item_id]:
                _drop_index(plan, old_tables[table_id], item)
    for table_id in sorted(old_tables.keys() - new_tables.keys()):
        table = old_tables[table_id]
        plan.add(
            sql.SQL('DROP TABLE {}').format(_qualified(schema_name, table['name'])),
            f'Drop table {table["name"]}',
            True,
            'destructive',
            {
                'kind': 'drop_table',
                'schema': schema_name,
                'table': table['name'],
                'definition': table,
            },
        )
        plan.warning(f'Dropping table {table["name"]} permanently removes its rows.')
    for table_id in sorted(old_tables.keys() & new_tables.keys()):
        before_table, after_table = old_tables[table_id], new_tables[table_id]
        if before_table['name'] != after_table['name']:
            plan.add(
                sql.SQL('ALTER TABLE {} RENAME TO {}').format(
                    _qualified(schema_name, before_table['name']), _identifier(after_table['name'])
                ),
                f'Rename table {before_table["name"]} to {after_table["name"]}',
                True,
                'metadata',
                {
                    'kind': 'rename_table',
                    'schema': schema_name,
                    'name': before_table['name'],
                    'new_name': after_table['name'],
                    'definition': after_table,
                },
            )
        old_columns, new_columns = _by_id(before_table['columns']), _by_id(after_table['columns'])
        for column_id, column in sorted(old_columns.items()):
            if column_id not in new_columns:
                plan.add(
                    sql.SQL('ALTER TABLE {} DROP COLUMN {}').format(
                        _qualified(schema_name, after_table['name']), _identifier(column['name'])
                    ),
                    f'Drop column {column["name"]}',
                    True,
                    'destructive',
                    {
                        'kind': 'drop_column',
                        'schema': schema_name,
                        'table': after_table['name'],
                        'name': column['name'],
                        'definition': column,
                    },
                )
                plan.warning(
                    f'Dropping {after_table["name"]}.{column["name"]} permanently removes its values.'
                )
        renamed = {
            column_id: (old_columns[column_id], new_columns[column_id])
            for column_id in old_columns.keys() & new_columns.keys()
            if old_columns[column_id]['name'] != new_columns[column_id]['name']
        }
        occupied = {
            column['name'] for column in before_table['columns'] if column['id'] in new_columns
        }
        staged_names: dict[str, str] = {}
        if any(after['name'] in occupied for _, after in renamed.values()):
            for column_id, (before_column, _) in sorted(renamed.items()):
                temporary = (
                    '__proteus_rename_' + hashlib.sha256(column_id.encode()).hexdigest()[:16]
                )
                all_names = occupied | {column['name'] for column in after_table['columns']}
                while temporary in all_names:
                    temporary += '_'
                plan.add(
                    sql.SQL('ALTER TABLE {} RENAME COLUMN {} TO {}').format(
                        _qualified(schema_name, after_table['name']),
                        _identifier(before_column['name']),
                        _identifier(temporary),
                    ),
                    f'Free column name {before_column["name"]} for a rename',
                    True,
                    'metadata',
                    {
                        'kind': 'rename_column',
                        'schema': schema_name,
                        'table': after_table['name'],
                        'name': before_column['name'],
                        'new_name': temporary,
                    },
                )
                staged_names[column_id] = temporary
                occupied.add(temporary)
        for column_id in sorted(old_columns.keys() & new_columns.keys()):
            before_column = old_columns[column_id]
            if column_id in staged_names:
                before_column = {**before_column, 'name': staged_names[column_id]}
            _alter_column(plan, before_table, after_table, before_column, new_columns[column_id])
        for column_id, column in sorted(new_columns.items()):
            if column_id not in old_columns:
                plan.add(
                    sql.SQL('ALTER TABLE {} ADD COLUMN {}').format(
                        _qualified(schema_name, after_table['name']), _column_definition(column)
                    ),
                    f'Add column {column["name"]}',
                    True,
                    'metadata',
                    {
                        'kind': 'add_column',
                        'schema': schema_name,
                        'table': after_table['name'],
                        'name': column['name'],
                        'definition': column,
                    },
                )
    for table_id in sorted(new_tables.keys() - old_tables.keys()):
        table = new_tables[table_id]
        definitions = sql.SQL(', ').join(_column_definition(column) for column in table['columns'])
        plan.add(
            sql.SQL('CREATE TABLE {} ({})').format(
                _qualified(schema_name, table['name']), definitions
            ),
            f'Create table {table["name"]}',
            True,
            'metadata',
            {
                'kind': 'create_table',
                'schema': schema_name,
                'table': table['name'],
                'definition': table,
            },
        )
    # All tables now exist. Establish referenced keys before foreign keys.
    for foreign_keys in (False, True):
        for table_id in sorted(new_tables):
            table = new_tables[table_id]
            old_table = old_tables.get(table_id)
            old_constraints = _by_id(old_table['constraints']) if old_table else {}
            for item_id, item in sorted(_by_id(table['constraints']).items()):
                if (item['kind'] == 'foreign_key') != foreign_keys:
                    continue
                if (
                    item_id not in old_constraints
                    or item != old_constraints[item_id]
                    or (table_id, item_id) in dropped_constraints
                ):
                    _add_constraint(plan, table, item, new_tables)
    for table_id in sorted(new_tables):
        table = new_tables[table_id]
        old_table = old_tables.get(table_id)
        old_indexes = _by_id(old_table['indexes']) if old_table else {}
        for item_id, item in sorted(_by_id(table['indexes']).items()):
            if item_id not in old_indexes or item != old_indexes[item_id]:
                _add_index(plan, table, item)
    if any(
        not step['transactional'] or step['operation']['kind'] == 'validate_constraint'
        for step in plan.steps
    ):
        plan.warning(
            'This plan commits in separate phases. A later failure may require recovery of earlier changes.'
        )
    return {'steps': plan.steps, 'warnings': plan.warnings}

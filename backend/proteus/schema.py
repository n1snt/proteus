"""Validation and canonicalization for Proteus schema snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from pglast import ast, parse_sql
from pglast.stream import RawStream

Snapshot = dict[str, Any]

_TYPE_PATTERN = re.compile(
    r'^(?:(boolean|bool)|(smallint|int2)|(integer|int|int4)|(bigint|int8)|'
    r'(real|float4)|(double precision|float8|float)|(numeric|decimal)(?:\((\d+)(?:\s*,\s*(\d+))?\))?|'
    r'(text)|(varchar|character varying)(?:\((\d+)\))?|(uuid)|(date)|(timestamp)(?:\s+(with|without)\s+time\s+zone)?|'
    r'(jsonb)|(bytea))$',
    re.IGNORECASE,
)


def _error(path: str, message: str) -> ValueError:
    return ValueError(f'{path}: {message}')


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(path, 'must be an object')
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _error(path, 'must be an array')
    return value


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _error(path, 'must be a UUID string')
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise _error(path, 'must be a UUID string') from exc


def _name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise _error(path, 'must be a non-empty PostgreSQL identifier')
    if len(value.encode('utf-8')) > 63:
        raise _error(path, 'must be at most 63 UTF-8 bytes')
    return value


def _type(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _error(path, 'must be a supported type string')
    match = _TYPE_PATTERN.fullmatch(' '.join(value.strip().split()))
    if not match:
        raise _error(path, 'is not a supported scalar type')
    groups = match.groups()
    if groups[0]:
        return 'boolean'
    if groups[1]:
        return 'smallint'
    if groups[2]:
        return 'integer'
    if groups[3]:
        return 'bigint'
    if groups[4]:
        return 'real'
    if groups[5]:
        return 'double precision'
    if groups[6]:
        precision = int(groups[7]) if groups[7] else None
        scale = int(groups[8]) if groups[8] else None
        if precision is not None and not 1 <= precision <= 1000:
            raise _error(path, 'numeric precision must be between 1 and 1000')
        if scale is not None and not 0 <= scale <= precision:
            raise _error(path, 'numeric scale must be between 0 and precision')
        return 'numeric' + (
            f'({precision}' + (f',{scale}' if scale is not None else '') + ')' if precision else ''
        )
    if groups[9]:
        return 'text'
    if groups[10]:
        length = int(groups[11]) if groups[11] else None
        if length is not None and not 1 <= length <= 10_485_760:
            raise _error(path, 'varchar length must be between 1 and 10485760')
        return f'varchar({length})' if length else 'varchar'
    if groups[12]:
        return 'uuid'
    if groups[13]:
        return 'date'
    if groups[14]:
        qualifier = groups[15]
        return 'timestamp' + (f' {qualifier} time zone' if qualifier else '')
    if groups[16]:
        return 'jsonb'
    return 'bytea'


def _foreign_key_types_compatible(local: str, referenced: str) -> bool:
    if local == referenced:
        return True
    integer_types = {'smallint', 'integer', 'bigint'}
    if local in integer_types and referenced in integer_types:
        return True
    if (local == 'text' or local.startswith('varchar')) and (
        referenced == 'text' or referenced.startswith('varchar')
    ):
        return True
    return local.startswith('numeric') and referenced.startswith('numeric')


def _expression_node(expression: str, path: str) -> Any:
    try:
        statements = parse_sql(f'SELECT {expression}')
    except Exception as exc:  # pglast exposes parser-specific exceptions.
        raise _error(path, 'is not valid SQL') from exc
    if len(statements) != 1:
        raise _error(path, 'must contain one expression')
    statement = statements[0].stmt
    if statement.__class__.__name__ != 'SelectStmt' or len(statement.targetList) != 1:
        raise _error(path, 'must contain one expression')
    statement_tree = statement(depth=2, skip_none=True)
    allowed = {'@', 'all', 'groupDistinct', 'limitOption', 'op', 'targetList'}
    if set(statement_tree) - allowed or statement.targetList[0].name is not None:
        raise _error(path, 'must contain one expression')
    return statement.targetList[0].val


def _tree(node: Any) -> dict[str, Any]:
    return node(depth=12, skip_none=True)


def _constant_tree(tree: dict[str, Any]) -> bool:
    tag = tree.get('@')
    if tag == 'A_Const':
        return True
    if tag == 'TypeCast':
        return _constant_tree(tree.get('arg', {}))
    if tag == 'A_Expr' and tree.get('kind', {}).get('name') == 'AEXPR_OP':
        return (
            tree.get('lexpr') is None
            and _constant_tree(tree.get('rexpr', {}))
            and tree.get('name', [{}])[0].get('sval') in {'+', '-'}
        )
    return False


def _supported_casts(tree: dict[str, Any]) -> bool:
    """Type casts are safe only when they name one of the modeled scalar types."""
    if tree.get('@') == 'TypeCast':
        type_name = tree.get('typeName', {})
        names = [item.get('sval') for item in type_name.get('names', ())]
        if len(names) not in {1, 2} or (len(names) == 2 and names[0] != 'pg_catalog'):
            return False
        try:
            _type(names[-1], 'cast')
        except ValueError:
            return False
        return _supported_casts(tree.get('arg', {}))
    if tree.get('@') == 'A_Expr' and tree.get('kind', {}).get('name') == 'AEXPR_OP':
        return _supported_casts(tree.get('rexpr', {}))
    return tree.get('@') == 'A_Const'


def _normalize_expression(node: Any) -> str:
    return RawStream()(node)


def _default(value: Any, data_type: str, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _error(path, 'must be a supported SQL expression or null')
    node = _expression_node(value, path)
    tree = _tree(node)
    if tree.get('@') == 'SQLValueFunction':
        if tree.get('op', {}).get('name') != 'SVFOP_CURRENT_TIMESTAMP' or not data_type.startswith(
            'timestamp'
        ):
            raise _error(path, 'only CURRENT_TIMESTAMP is supported as a SQL value function')
    elif not _constant_tree(tree) or not _supported_casts(tree):
        raise _error(path, 'must be a typed literal, NULL, or CURRENT_TIMESTAMP')
    return _normalize_expression(node)


def _column_reference(tree: dict[str, Any], names: set[str]) -> bool:
    if tree.get('@') != 'ColumnRef':
        return False
    fields = tree.get('fields', ())
    return len(fields) == 1 and fields[0].get('@') == 'String' and fields[0].get('sval') in names


def _check_expression(value: Any, column_names: set[str], path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _error(path, 'must be a supported check expression or null')
    tree = _tree(_expression_node(value, path))
    if tree.get('@') == 'NullTest' and _column_reference(tree.get('arg', {}), column_names):
        return _normalize_expression(_expression_node(value, path))
    if tree.get('@') == 'A_Expr' and tree.get('kind', {}).get('name') == 'AEXPR_OP':
        operator = tree.get('name', [{}])[0].get('sval')
        if (
            operator in {'=', '<>', '!=', '<', '<=', '>', '>='}
            and _column_reference(tree.get('lexpr', {}), column_names)
            and _constant_tree(tree.get('rexpr', {}))
            and _supported_casts(tree.get('rexpr', {}))
        ):
            return _normalize_expression(_expression_node(value, path))
    raise _error(path, 'must be a column comparison to a literal or a null check')


def _ensure_keys(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise _error(path, f'contains unsupported fields: {", ".join(sorted(unexpected))}')


def rewrite_check_expression(expression: str, renames: dict[str, str]) -> str:
    """Rename a parsed column reference without touching literal text."""
    node = _expression_node(expression, 'check expression')
    reference = (
        node.arg
        if isinstance(node, ast.NullTest)
        else node.lexpr
        if isinstance(node, ast.A_Expr)
        else None
    )
    if isinstance(reference, ast.ColumnRef) and len(reference.fields) == 1:
        field = reference.fields[0]
        if isinstance(field, ast.String) and field.sval in renames:
            reference.fields = (ast.String(sval=renames[field.sval]),)
    return _normalize_expression(node)


def rebind_unchanged_checks(before: Snapshot, after: Snapshot) -> Snapshot:
    """Keep unchanged checks bound to their column identities during draft renames."""
    result = copy.deepcopy(after)
    originals = {table['id']: table for table in before['tables']}
    for table in _list(result.get('tables'), 'snapshot.tables'):
        table = _object(table, 'table')
        original = originals.get(table.get('id'))
        if original is None:
            continue
        names = {
            column.get('id'): column.get('name')
            for column in _list(table.get('columns'), 'columns')
            if isinstance(column, dict)
        }
        renames = {
            column['name']: names[column['id']]
            for column in original['columns']
            if column['id'] in names and names[column['id']] != column['name']
        }
        checks = {item['id']: item for item in original['constraints'] if item['kind'] == 'check'}
        for item in table.get('constraints', []):
            if not isinstance(item, dict):
                continue
            previous = checks.get(item.get('id'))
            if previous and item.get('expression') == previous['expression'] and renames:
                item['expression'] = rewrite_check_expression(previous['expression'], renames)
    return result


def validate_snapshot(snapshot: Any) -> Snapshot:
    """Return a normalized supported snapshot, or raise ValueError."""
    root = _object(snapshot, 'snapshot')
    _ensure_keys(root, {'tables'}, 'snapshot')
    tables_input = _list(root.get('tables'), 'snapshot.tables')
    tables: list[dict[str, Any]] = []
    table_ids: set[str] = set()
    table_names: set[str] = set()
    for table_index, raw_table in enumerate(tables_input):
        path = f'snapshot.tables[{table_index}]'
        table = _object(raw_table, path)
        _ensure_keys(table, {'id', 'name', 'columns', 'constraints', 'indexes'}, path)
        table_id = _id(table.get('id'), f'{path}.id')
        table_name = _name(table.get('name'), f'{path}.name')
        if table_id in table_ids or table_name in table_names:
            raise _error(path, 'has a duplicate table ID or name')
        table_ids.add(table_id)
        table_names.add(table_name)
        raw_columns = _list(table.get('columns', []), f'{path}.columns')
        columns: list[dict[str, Any]] = []
        column_ids: set[str] = set()
        column_names: set[str] = set()
        for index, raw_column in enumerate(raw_columns):
            column_path = f'{path}.columns[{index}]'
            column = _object(raw_column, column_path)
            _ensure_keys(
                column, {'id', 'name', 'data_type', 'nullable', 'default', 'identity'}, column_path
            )
            column_id = _id(column.get('id'), f'{column_path}.id')
            column_name = _name(column.get('name'), f'{column_path}.name')
            data_type = _type(column.get('data_type'), f'{column_path}.data_type')
            nullable = column.get('nullable')
            if not isinstance(nullable, bool):
                raise _error(f'{column_path}.nullable', 'must be a boolean')
            identity = column.get('identity')
            if identity not in {None, 'always', 'by_default'}:
                raise _error(f'{column_path}.identity', 'must be always, by_default, or null')
            if column_id in column_ids or column_name in column_names:
                raise _error(column_path, 'has a duplicate column ID or name')
            column_ids.add(column_id)
            column_names.add(column_name)
            default = _default(column.get('default'), data_type, f'{column_path}.default')
            if identity is not None and (nullable or default is not None):
                raise _error(
                    column_path, 'identity columns must be non-null and cannot declare a default'
                )
            columns.append(
                {
                    'id': column_id,
                    'name': column_name,
                    'data_type': data_type,
                    'nullable': nullable,
                    'default': default,
                    'identity': identity,
                }
            )
        tables.append(
            {
                'id': table_id,
                'name': table_name,
                'columns': columns,
                'constraints': [],
                'indexes': [],
            }
        )

    table_by_id = {table['id']: table for table in tables}
    for table_index, raw_table in enumerate(tables_input):
        path = f'snapshot.tables[{table_index}]'
        table = tables[table_index]
        column_ids = {column['id'] for column in table['columns']}
        column_names = {column['name'] for column in table['columns']}
        constraint_ids: set[str] = set()
        constraint_names: set[str] = set()
        for index, raw_constraint in enumerate(
            _list(_object(raw_table, path).get('constraints', []), f'{path}.constraints')
        ):
            constraint_path = f'{path}.constraints[{index}]'
            constraint = _object(raw_constraint, constraint_path)
            _ensure_keys(
                constraint,
                {
                    'id',
                    'name',
                    'kind',
                    'columns',
                    'reference_table',
                    'reference_columns',
                    'on_delete',
                    'on_update',
                    'expression',
                },
                constraint_path,
            )
            constraint_id = _id(constraint.get('id'), f'{constraint_path}.id')
            name = _name(constraint.get('name'), f'{constraint_path}.name')
            kind = constraint.get('kind')
            if kind not in {'primary_key', 'unique', 'foreign_key', 'check'}:
                raise _error(f'{constraint_path}.kind', 'is not supported')
            columns = [
                _id(value, f'{constraint_path}.columns[{position}]')
                for position, value in enumerate(
                    _list(constraint.get('columns', []), f'{constraint_path}.columns')
                )
            ]
            if len(columns) != len(set(columns)) or any(
                value not in column_ids for value in columns
            ):
                raise _error(
                    f'{constraint_path}.columns',
                    'must reference distinct columns in the same table',
                )
            if kind != 'check' and not columns:
                raise _error(f'{constraint_path}.columns', 'cannot be empty')
            reference_table = constraint.get('reference_table')
            reference_columns = [
                _id(value, f'{constraint_path}.reference_columns[{position}]')
                for position, value in enumerate(
                    _list(
                        constraint.get('reference_columns', []),
                        f'{constraint_path}.reference_columns',
                    )
                )
            ]
            on_delete = constraint.get('on_delete')
            on_update = constraint.get('on_update')
            expression = _check_expression(
                constraint.get('expression'), column_names, f'{constraint_path}.expression'
            )
            if kind == 'foreign_key':
                reference_table = _id(reference_table, f'{constraint_path}.reference_table')
                referenced = table_by_id.get(reference_table)
                if referenced is None:
                    raise _error(
                        f'{constraint_path}.reference_table', 'must reference a tracked table'
                    )
                if (
                    len(columns) != len(reference_columns)
                    or not reference_columns
                    or any(
                        value not in {column['id'] for column in referenced['columns']}
                        for value in reference_columns
                    )
                ):
                    raise _error(
                        f'{constraint_path}.reference_columns',
                        'must match local columns on the referenced table',
                    )
                if on_delete not in {
                    None,
                    'NO ACTION',
                    'RESTRICT',
                    'CASCADE',
                    'SET NULL',
                    'SET DEFAULT',
                } or on_update not in {
                    None,
                    'NO ACTION',
                    'RESTRICT',
                    'CASCADE',
                    'SET NULL',
                    'SET DEFAULT',
                }:
                    raise _error(constraint_path, 'has an unsupported foreign-key action')
                if expression is not None:
                    raise _error(f'{constraint_path}.expression', 'is not allowed on a foreign key')
            elif (
                reference_table is not None
                or reference_columns
                or on_delete is not None
                or on_update is not None
            ):
                raise _error(
                    constraint_path, 'has foreign-key fields on a non-foreign-key constraint'
                )
            if kind == 'check':
                if expression is None:
                    raise _error(
                        f'{constraint_path}.expression', 'is required for a check constraint'
                    )
            elif expression is not None:
                raise _error(
                    f'{constraint_path}.expression', 'is only allowed on a check constraint'
                )
            if constraint_id in constraint_ids or name in constraint_names:
                raise _error(constraint_path, 'has a duplicate constraint ID or name')
            constraint_ids.add(constraint_id)
            constraint_names.add(name)
            table['constraints'].append(
                {
                    'id': constraint_id,
                    'name': name,
                    'kind': kind,
                    'columns': columns,
                    'reference_table': reference_table,
                    'reference_columns': reference_columns,
                    'on_delete': on_delete,
                    'on_update': on_update,
                    'expression': expression,
                }
            )
        index_ids: set[str] = set()
        index_names: set[str] = set()
        for index, raw_index in enumerate(
            _list(_object(raw_table, path).get('indexes', []), f'{path}.indexes')
        ):
            index_path = f'{path}.indexes[{index}]'
            item = _object(raw_index, index_path)
            _ensure_keys(item, {'id', 'name', 'columns', 'unique'}, index_path)
            index_id = _id(item.get('id'), f'{index_path}.id')
            name = _name(item.get('name'), f'{index_path}.name')
            columns = [
                _id(value, f'{index_path}.columns[{position}]')
                for position, value in enumerate(
                    _list(item.get('columns', []), f'{index_path}.columns')
                )
            ]
            if (
                not columns
                or len(columns) != len(set(columns))
                or any(value not in column_ids for value in columns)
            ):
                raise _error(
                    f'{index_path}.columns', 'must reference distinct columns in the same table'
                )
            if not isinstance(item.get('unique'), bool):
                raise _error(f'{index_path}.unique', 'must be a boolean')
            if index_id in index_ids or name in index_names or name in constraint_names:
                raise _error(index_path, 'has a duplicate index ID or relation name')
            index_ids.add(index_id)
            index_names.add(name)
            table['indexes'].append(
                {'id': index_id, 'name': name, 'columns': columns, 'unique': item['unique']}
            )
    relation_names = set(table_names)
    for table in tables:
        columns_by_id = {column['id']: column for column in table['columns']}
        for item in table['constraints']:
            if item['kind'] == 'primary_key' and any(
                columns_by_id[column_id]['nullable'] for column_id in item['columns']
            ):
                raise _error(
                    f'table {table["name"]} constraint {item["name"]}',
                    'primary key columns must be non-null',
                )
            if item['kind'] in {'primary_key', 'unique'}:
                if item['name'] in relation_names:
                    raise _error(item['name'], 'duplicates a relation name in this schema')
                relation_names.add(item['name'])
            if item['kind'] != 'foreign_key':
                continue
            referenced = table_by_id[item['reference_table']]
            referenced_columns = {column['id']: column for column in referenced['columns']}
            if tuple(item['reference_columns']) not in {
                tuple(key['columns'])
                for key in referenced['constraints']
                if key['kind'] in {'primary_key', 'unique'}
            } | {tuple(index['columns']) for index in referenced['indexes'] if index['unique']}:
                raise _error(
                    f'table {table["name"]} constraint {item["name"]}',
                    'foreign-key reference columns must be a primary key or unique key',
                )
            for local_id, reference_id in zip(
                item['columns'], item['reference_columns'], strict=True
            ):
                if not _foreign_key_types_compatible(
                    columns_by_id[local_id]['data_type'],
                    referenced_columns[reference_id]['data_type'],
                ):
                    raise _error(
                        f'table {table["name"]} constraint {item["name"]}',
                        'foreign-key column types are not compatible',
                    )
        for item in table['indexes']:
            if item['name'] in relation_names:
                raise _error(item['name'], 'duplicates a relation name in this schema')
            relation_names.add(item['name'])
    return {'tables': tables}


def _structural_snapshot(snapshot: Snapshot) -> dict[str, Any]:
    tables_by_id = {table['id']: table for table in snapshot['tables']}

    def columns(table: dict[str, Any]) -> dict[str, str]:
        return {column['id']: column['name'] for column in table['columns']}

    result = []
    for table in snapshot['tables']:
        names = columns(table)
        constraints = []
        for item in table['constraints']:
            value = {key: copy.deepcopy(value) for key, value in item.items() if key != 'id'}
            value['columns'] = [names[column_id] for column_id in item['columns']]
            if item['reference_table'] is not None:
                reference = tables_by_id[item['reference_table']]
                reference_names = columns(reference)
                value['reference_table'] = reference['name']
                value['reference_columns'] = [
                    reference_names[column_id] for column_id in item['reference_columns']
                ]
            constraints.append(value)
        indexes = [
            {key: value for key, value in item.items() if key != 'id' and key != 'columns'}
            | {'columns': [names[column_id] for column_id in item['columns']]}
            for item in table['indexes']
        ]
        result.append(
            {
                'name': table['name'],
                'columns': sorted(
                    [
                        {key: value for key, value in column.items() if key != 'id'}
                        for column in table['columns']
                    ],
                    key=lambda value: value['name'],
                ),
                'constraints': sorted(
                    constraints, key=lambda value: (value['name'], value['kind'])
                ),
                'indexes': sorted(indexes, key=lambda value: value['name']),
            }
        )
    return {'tables': sorted(result, key=lambda value: value['name'])}


def fingerprint(snapshot: Any) -> str:
    """Return a stable structural checksum that deliberately excludes lineage IDs."""
    normalized = validate_snapshot(snapshot)
    encoded = json.dumps(
        _structural_snapshot(normalized), sort_keys=True, separators=(',', ':'), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode()).hexdigest()

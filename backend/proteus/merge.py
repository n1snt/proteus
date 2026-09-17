"""Rename-aware, property-level three-way schema merging."""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from .schema import rewrite_check_expression, validate_snapshot


def _by_id(values: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {value['id']: value for value in values}


def _conflict_id(object_type: str, table: str | None, object_id: str, property_name: str) -> str:
    raw = f'{object_type}|{table or ""}|{object_id}|{property_name}'.encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _equivalent(
    left: dict[str, Any], right: dict[str, Any], ignored: set[str] | None = None
) -> bool:
    ignored = {'id'} if ignored is None else ignored | {'id'}
    return {key: value for key, value in left.items() if key not in ignored} == {
        key: value for key, value in right.items() if key not in ignored
    }


def _equivalent_table(
    left: dict[str, Any],
    right: dict[str, Any],
    left_tables: dict[str, dict[str, Any]],
    right_tables: dict[str, dict[str, Any]],
) -> bool:
    """Compare independently added tables by names rather than their new lineage IDs."""

    def structural(table: dict[str, Any], tables: dict[str, dict[str, Any]]) -> dict[str, Any]:
        names = {column['id']: column['name'] for column in table['columns']}

        def constraint(item: dict[str, Any]) -> dict[str, Any]:
            value = {
                key: value
                for key, value in item.items()
                if key not in {'id', 'columns', 'reference_columns', 'reference_table'}
            } | {'columns': [names[value] for value in item['columns']]}
            if item['reference_table'] is not None:
                reference = tables[item['reference_table']]
                reference_names = {column['id']: column['name'] for column in reference['columns']}
                value['reference_table'] = reference['name']
                value['reference_columns'] = [
                    reference_names[value] for value in item['reference_columns']
                ]
            return value

        return {
            'name': table['name'],
            'columns': sorted(
                [
                    {key: value for key, value in column.items() if key != 'id'}
                    for column in table['columns']
                ],
                key=lambda value: value['name'],
            ),
            'constraints': sorted(
                [constraint(item) for item in table['constraints']],
                key=lambda value: (value['name'], value['kind']),
            ),
            'indexes': sorted(
                [
                    {key: value for key, value in item.items() if key not in {'id', 'columns'}}
                    | {'columns': [names[value] for value in item['columns']]}
                    for item in table['indexes']
                ],
                key=lambda value: value['name'],
            ),
        }

    return structural(left, left_tables) == structural(right, right_tables)


class _Merger:
    def __init__(
        self, source: dict[str, Any], target: dict[str, Any], resolutions: dict[str, str]
    ) -> None:
        invalid = {
            key: value for key, value in resolutions.items() if value not in {'source', 'target'}
        }
        if invalid:
            raise ValueError('resolutions must use source or target')
        self.source = source
        self.target = target
        self.resolutions = resolutions
        self.conflicts: list[dict[str, Any]] = []

    def choose(
        self,
        object_type: str,
        table: str | None,
        item: dict[str, Any],
        property_name: str,
        reason: str,
        base: Any,
        source: Any,
        target: Any,
    ) -> Any:
        conflict_id = _conflict_id(object_type, table, item['id'], property_name)
        selected = self.resolutions.get(conflict_id)
        self.conflicts.append(
            {
                'id': conflict_id,
                'object_type': object_type,
                'object_name': item.get('name', item['id']),
                'property': property_name,
                'reason': reason,
                'base': copy.deepcopy(base),
                'source': copy.deepcopy(source),
                'target': copy.deepcopy(target),
                'resolution': selected,
            }
        )
        return copy.deepcopy(source if selected == 'source' else target)

    def value(
        self,
        object_type: str,
        table: str | None,
        item: dict[str, Any],
        property_name: str,
        base: Any,
        source: Any,
        target: Any,
    ) -> Any:
        source_changed, target_changed = source != base, target != base
        if not source_changed:
            return copy.deepcopy(target)
        if not target_changed or source == target:
            return copy.deepcopy(source)
        return self.choose(
            object_type,
            table,
            item,
            property_name,
            'both sides changed this property',
            base,
            source,
            target,
        )

    def existing_item(
        self,
        object_type: str,
        table: str | None,
        base: dict[str, Any],
        source: dict[str, Any] | None,
        target: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if source is None and target is None:
            return None
        if source is None:
            if target == base:
                return None
            chosen = self.choose(
                object_type,
                table,
                base,
                'existence',
                'source removed the object while target changed it',
                base,
                None,
                target,
            )
            return chosen
        if target is None:
            if source == base:
                return None
            chosen = self.choose(
                object_type,
                table,
                base,
                'existence',
                'target removed the object while source changed it',
                base,
                source,
                None,
            )
            return chosen
        result = {'id': base['id']}
        for key in base:
            if key != 'id':
                result[key] = self.value(
                    object_type,
                    table,
                    result | {'name': source.get('name', base.get('name', base['id']))},
                    key,
                    base.get(key),
                    source.get(key),
                    target.get(key),
                )
        return result

    def collection(
        self,
        object_type: str,
        table_name: str | None,
        base: list[dict[str, Any]],
        source: list[dict[str, Any]],
        target: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        base_by_id, source_by_id, target_by_id = _by_id(base), _by_id(source), _by_id(target)
        result: list[dict[str, Any]] = []
        remap: dict[str, str] = {}
        for item_id in sorted(base_by_id):
            merged = self.existing_item(
                object_type,
                table_name,
                base_by_id[item_id],
                source_by_id.get(item_id),
                target_by_id.get(item_id),
            )
            if merged is not None:
                result.append(merged)
                remap[item_id] = merged['id']
        source_added = [item for item_id, item in source_by_id.items() if item_id not in base_by_id]
        target_added = [item for item_id, item in target_by_id.items() if item_id not in base_by_id]
        target_by_name = {item['name']: item for item in target_added}
        consumed_target: set[str] = set()
        for item in sorted(source_added, key=lambda value: (value['name'], value['id'])):
            other = target_by_name.get(item['name'])
            if other is None:
                result.append(copy.deepcopy(item))
                remap[item['id']] = item['id']
            elif _equivalent(item, other):
                result.append(copy.deepcopy(other))
                remap[item['id']] = other['id']
                consumed_target.add(other['id'])
            else:
                selected = self.choose(
                    object_type,
                    table_name,
                    item,
                    'addition',
                    'both sides added different objects with the same name',
                    None,
                    item,
                    other,
                )
                if selected is not None:
                    result.append(selected)
                    remap[item['id']] = selected['id']
                consumed_target.add(other['id'])
        for item in sorted(target_added, key=lambda value: (value['name'], value['id'])):
            if item['id'] not in consumed_target:
                result.append(copy.deepcopy(item))
        return result, remap


def _rewrite_constraints(
    constraints: list[dict[str, Any]], column_remap: dict[str, str], table_remap: dict[str, str]
) -> list[dict[str, Any]]:
    rewritten = copy.deepcopy(constraints)
    for item in rewritten:
        item['columns'] = [column_remap.get(value, value) for value in item['columns']]
        item['reference_columns'] = [
            column_remap.get(value, value) for value in item['reference_columns']
        ]
        if item['reference_table'] is not None:
            item['reference_table'] = table_remap.get(
                item['reference_table'], item['reference_table']
            )
    return rewritten


def _merge_table(
    merger: _Merger,
    base: dict[str, Any],
    source: dict[str, Any],
    target: dict[str, Any],
    table_remap: dict[str, str],
    column_remap: dict[str, str],
) -> dict[str, Any]:
    table = {
        'id': base['id'],
        'name': merger.value(
            'table', None, base, 'name', base['name'], source['name'], target['name']
        ),
    }
    columns, source_column_remap = merger.collection(
        'column', table['name'], base['columns'], source['columns'], target['columns']
    )
    merged_names = {column['id']: column['name'] for column in columns}

    def bound_constraints(
        original: dict[str, Any], remap: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        remap = remap or {}
        renames = {
            column['name']: merged_names[remap.get(column['id'], column['id'])]
            for column in original['columns']
            if remap.get(column['id'], column['id']) in merged_names
        }
        constraints = copy.deepcopy(original['constraints'])
        for constraint in constraints:
            if constraint['kind'] == 'check':
                constraint['expression'] = rewrite_check_expression(
                    constraint['expression'], renames
                )
        return constraints

    # A matching independently added column uses the target identity in dependent objects.
    source_constraints = _rewrite_constraints(
        bound_constraints(source, column_remap | source_column_remap),
        column_remap | source_column_remap,
        table_remap,
    )
    target_constraints = _rewrite_constraints(bound_constraints(target), {}, table_remap)
    constraints, _ = merger.collection(
        'constraint', table['name'], bound_constraints(base), source_constraints, target_constraints
    )
    source_indexes = copy.deepcopy(source['indexes'])
    for item in source_indexes:
        item['columns'] = [source_column_remap.get(value, value) for value in item['columns']]
    indexes, _ = merger.collection(
        'index', table['name'], base['indexes'], source_indexes, target['indexes']
    )
    table.update({'columns': columns, 'constraints': constraints, 'indexes': indexes})
    return table


def _dependency_conflicts(
    merger: _Merger, snapshot: dict[str, Any], source: dict[str, Any], target: dict[str, Any]
) -> None:
    tables = _by_id(snapshot['tables'])
    source_tables, target_tables = _by_id(source['tables']), _by_id(target['tables'])
    for table in list(snapshot['tables']):
        kept = []
        for constraint in table['constraints']:
            reference = constraint['reference_table']
            if constraint['kind'] != 'foreign_key' or reference in tables:
                kept.append(constraint)
                continue
            source_has, target_has = reference in source_tables, reference in target_tables
            selected = merger.choose(
                'constraint',
                table['name'],
                constraint,
                'dependencies',
                'foreign key references a table removed by the other side',
                None,
                constraint if source_has else None,
                constraint if target_has else None,
            )
            # Choosing the side with the referenced table restores that table. Choosing the
            # other side removes only the conflicting FK, preserving unrelated table edits.
            if selected is not None and (source_has or target_has):
                restored = (
                    source_tables.get(reference)
                    if merger.resolutions.get(merger.conflicts[-1]['id']) == 'source'
                    else target_tables.get(reference)
                )
                if restored is not None and reference not in tables:
                    snapshot['tables'].append(copy.deepcopy(restored))
                    tables[reference] = restored
                kept.append(constraint)
        table['constraints'] = kept


def _default_matches_type(default: str | None, data_type: str) -> bool:
    if default is None or default == 'NULL':
        return True
    if data_type in {'smallint', 'integer', 'bigint'}:
        return bool(re.fullmatch(r'[+-]?\d+', default)) or default.endswith(f' AS {data_type})')
    if data_type == 'boolean':
        return default in {'TRUE', 'FALSE'} or default.endswith(' AS boolean)')
    if data_type.startswith('numeric'):
        return bool(re.fullmatch(r'[+-]?\d+(?:\.\d+)?', default)) or ' AS numeric' in default
    return True


def _key_item(table: dict[str, Any], columns: list[str]) -> tuple[str, dict[str, Any]] | None:
    for item in table['constraints']:
        if item['kind'] in {'primary_key', 'unique'} and item['columns'] == columns:
            return 'constraints', item
    for item in table['indexes']:
        if item['unique'] and item['columns'] == columns:
            return 'indexes', item
    return None


def _semantic_conflicts(
    merger: _Merger,
    snapshot: dict[str, Any],
    base: dict[str, Any],
    source: dict[str, Any],
    target: dict[str, Any],
) -> None:
    base_tables, source_tables, target_tables = (
        _by_id(base['tables']),
        _by_id(source['tables']),
        _by_id(target['tables']),
    )
    candidate_tables = _by_id(snapshot['tables'])
    for table in snapshot['tables']:
        base_table, source_table, target_table = (
            base_tables.get(table['id']),
            source_tables.get(table['id']),
            target_tables.get(table['id']),
        )
        if base_table is None or source_table is None or target_table is None:
            continue
        base_columns, source_columns, target_columns = (
            _by_id(base_table['columns']),
            _by_id(source_table['columns']),
            _by_id(target_table['columns']),
        )
        candidate_columns = _by_id(table['columns'])
        for constraint in list(table['constraints']):
            if constraint['kind'] != 'foreign_key':
                continue
            reference_table = candidate_tables[constraint['reference_table']]
            if _key_item(reference_table, constraint['reference_columns']) is not None:
                continue
            source_reference = source_tables.get(constraint['reference_table'])
            target_reference = target_tables.get(constraint['reference_table'])
            source_key = (
                _key_item(source_reference, constraint['reference_columns'])
                if source_reference is not None
                else None
            )
            target_key = (
                _key_item(target_reference, constraint['reference_columns'])
                if target_reference is not None
                else None
            )
            merger.choose(
                'constraint',
                table['name'],
                constraint,
                'reference_key',
                'foreign key needs a referenced primary key or unique key',
                None,
                {
                    'foreign_key': next(
                        (
                            item
                            for item in source_table['constraints']
                            if item['id'] == constraint['id']
                        ),
                        None,
                    ),
                    'reference_key': source_key[1] if source_key else None,
                },
                {
                    'foreign_key': next(
                        (
                            item
                            for item in target_table['constraints']
                            if item['id'] == constraint['id']
                        ),
                        None,
                    ),
                    'reference_key': target_key[1] if target_key else None,
                },
            )
            conflict_id = merger.conflicts[-1]['id']
            selected_key = (
                source_key if merger.resolutions.get(conflict_id) == 'source' else target_key
            )
            if selected_key is None:
                table['constraints'].remove(constraint)
            elif all(
                item['id'] != selected_key[1]['id'] for item in reference_table[selected_key[0]]
            ):
                reference_table[selected_key[0]].append(copy.deepcopy(selected_key[1]))
        for constraint in list(table['constraints']):
            if constraint['kind'] != 'primary_key':
                continue
            for column_id in constraint['columns']:
                if column_id not in source_columns or column_id not in target_columns:
                    continue
                column = candidate_columns[column_id]
                if not column['nullable']:
                    continue
                source_column, target_column = source_columns[column_id], target_columns[column_id]
                merger.choose(
                    'column',
                    table['name'],
                    column,
                    'nullable_for_primary_key',
                    f'primary key {constraint["name"]} requires a non-null column',
                    base_columns[column_id]['nullable'],
                    source_column['nullable'],
                    target_column['nullable'],
                )
                conflict_id = merger.conflicts[-1]['id']
                if merger.resolutions.get(conflict_id) == 'source':
                    table['constraints'].remove(constraint)
                else:
                    column['nullable'] = target_column['nullable']
        for column_id, column in candidate_columns.items():
            if column_id not in source_columns or column_id not in target_columns:
                continue
            source_column, target_column = source_columns[column_id], target_columns[column_id]
            if column['identity'] is not None and (
                column['nullable'] or column['default'] is not None
            ):
                merger.choose(
                    'column',
                    table['name'],
                    column,
                    'identity_definition',
                    'identity columns cannot be nullable or declare a default',
                    {
                        'identity': base_columns[column_id]['identity'],
                        'nullable': base_columns[column_id]['nullable'],
                        'default': base_columns[column_id]['default'],
                    },
                    {
                        'identity': source_column['identity'],
                        'nullable': source_column['nullable'],
                        'default': source_column['default'],
                    },
                    {
                        'identity': target_column['identity'],
                        'nullable': target_column['nullable'],
                        'default': target_column['default'],
                    },
                )
                conflict_id = merger.conflicts[-1]['id']
                selected = (
                    source_column
                    if merger.resolutions.get(conflict_id) == 'source'
                    else target_column
                )
                column['identity'] = selected['identity']
                column['nullable'] = selected['nullable']
                column['default'] = selected['default']
            if _default_matches_type(column['default'], column['data_type']):
                continue
            source_changed_default = source_column['default'] != base_columns[column_id]['default']
            target_changed_default = target_column['default'] != base_columns[column_id]['default']
            source_changed_type = source_column['data_type'] != base_columns[column_id]['data_type']
            target_changed_type = target_column['data_type'] != base_columns[column_id]['data_type']
            if not (
                (source_changed_default and target_changed_type)
                or (target_changed_default and source_changed_type)
            ):
                continue
            merger.choose(
                'column',
                table['name'],
                column,
                'default_for_data_type',
                'default is not compatible with the merged column type',
                {
                    'data_type': base_columns[column_id]['data_type'],
                    'default': base_columns[column_id]['default'],
                },
                {'data_type': source_column['data_type'], 'default': source_column['default']},
                {'data_type': target_column['data_type'], 'default': target_column['default']},
            )
            conflict_id = merger.conflicts[-1]['id']
            selected = (
                source_column if merger.resolutions.get(conflict_id) == 'source' else target_column
            )
            column['data_type'] = selected['data_type']
            column['default'] = selected['default']


def merge_snapshots(
    base: Any, source: Any, target: Any, resolutions: dict[str, str] | None = None
) -> dict[str, Any]:
    """Merge source into target without discarding compatible property changes."""
    base_snapshot, source_snapshot, target_snapshot = (
        validate_snapshot(base),
        validate_snapshot(source),
        validate_snapshot(target),
    )
    merger = _Merger(source_snapshot, target_snapshot, resolutions or {})
    base_tables, source_tables, target_tables = (
        _by_id(base_snapshot['tables']),
        _by_id(source_snapshot['tables']),
        _by_id(target_snapshot['tables']),
    )
    result: list[dict[str, Any]] = []
    table_remap: dict[str, str] = {table_id: table_id for table_id in base_tables}
    for table in source_tables.values():
        if table['id'] not in base_tables:
            other = next(
                (
                    candidate
                    for candidate in target_tables.values()
                    if candidate['id'] not in base_tables and candidate['name'] == table['name']
                ),
                None,
            )
            table_remap[table['id']] = (
                other['id']
                if other is not None
                and _equivalent_table(table, other, source_tables, target_tables)
                else table['id']
            )
    column_remap: dict[str, str] = {}
    for source_id, source_table in source_tables.items():
        target_table = target_tables.get(table_remap.get(source_id, source_id))
        if target_table is None:
            continue
        source_columns, target_columns = (
            _by_id(source_table['columns']),
            _by_id(target_table['columns']),
        )
        target_by_name = {column['name']: column for column in target_columns.values()}
        for column_id, column in source_columns.items():
            other = target_columns.get(column_id) or target_by_name.get(column['name'])
            if other is not None and _equivalent(column, other):
                column_remap[column_id] = other['id']
    for table_id in sorted(base_tables):
        source_table, target_table = source_tables.get(table_id), target_tables.get(table_id)
        if source_table is not None and target_table is not None:
            merged = _merge_table(
                merger, base_tables[table_id], source_table, target_table, table_remap, column_remap
            )
        else:
            merged = merger.existing_item(
                'table', None, base_tables[table_id], source_table, target_table
            )
        if merged is None:
            continue
        result.append(merged)
        table_remap[table_id] = merged['id']
    source_added = [
        table for table_id, table in source_tables.items() if table_id not in base_tables
    ]
    target_added = [
        table for table_id, table in target_tables.items() if table_id not in base_tables
    ]
    target_by_name = {table['name']: table for table in target_added}
    used_targets: set[str] = set()
    for table in sorted(source_added, key=lambda value: (value['name'], value['id'])):
        other = target_by_name.get(table['name'])
        if other is None:
            selected = copy.deepcopy(table)
            selected['constraints'] = _rewrite_constraints(
                selected['constraints'], column_remap, table_remap
            )
            result.append(selected)
            table_remap[table['id']] = table['id']
        elif _equivalent_table(table, other, source_tables, target_tables):
            result.append(copy.deepcopy(other))
            table_remap[table['id']] = other['id']
            used_targets.add(other['id'])
        else:
            selected = merger.choose(
                'table',
                None,
                table,
                'addition',
                'both sides added different tables with the same name',
                None,
                table,
                other,
            )
            if selected is not None:
                result.append(selected)
                table_remap[table['id']] = selected['id']
            used_targets.add(other['id'])
    for table in sorted(target_added, key=lambda value: (value['name'], value['id'])):
        if table['id'] not in used_targets:
            result.append(copy.deepcopy(table))
    snapshot = {'tables': result}
    _dependency_conflicts(merger, snapshot, source_snapshot, target_snapshot)
    _semantic_conflicts(merger, snapshot, base_snapshot, source_snapshot, target_snapshot)
    try:
        snapshot = validate_snapshot(snapshot)
    except ValueError as exc:
        # A preview must remain usable even if a future invariant has no dedicated
        # merge rule yet. Both original sides were independently validated.
        snapshot = merger.choose(
            'schema',
            None,
            {'id': 'schema', 'name': 'Whole schema'},
            'definition',
            f'These definitions cannot be combined: {exc}. Choosing a side replaces the whole schema result and may discard other changes.',
            base_snapshot,
            source_snapshot,
            target_snapshot,
        )
    return {'snapshot': snapshot, 'conflicts': merger.conflicts}

from __future__ import annotations

import copy
import uuid

import pytest
from proteus.diff import diff_snapshots
from proteus.merge import merge_snapshots
from proteus.planner import build_plan
from proteus.schema import fingerprint, rebind_unchanged_checks, validate_snapshot


def uid() -> str:
    return str(uuid.uuid4())


def table(name: str = 'users') -> dict:
    table_id, column_id = uid(), uid()
    return {
        'id': table_id,
        'name': name,
        'columns': [
            {
                'id': column_id,
                'name': 'email',
                'data_type': 'varchar(120)',
                'nullable': True,
                'default': None,
                'identity': None,
            }
        ],
        'constraints': [],
        'indexes': [],
    }


def test_validation_normalizes_aliases_and_fingerprint_ignores_ids() -> None:
    first = {'tables': [table()]}
    second = copy.deepcopy(first)
    second['tables'][0]['id'] = uid()
    second['tables'][0]['columns'][0]['id'] = uid()

    normalized = validate_snapshot(first)

    assert normalized['tables'][0]['columns'][0]['data_type'] == 'varchar(120)'
    assert fingerprint(first) == fingerprint(second)


@pytest.mark.parametrize('default', ['now()', "'safe'; DROP TABLE users", '1 FROM users'])
def test_validation_rejects_unsafe_defaults(default: str) -> None:
    snapshot = {'tables': [table()]}
    snapshot['tables'][0]['columns'][0]['default'] = default

    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_diff_reports_rename_as_semantic_column_change() -> None:
    before = {'tables': [table()]}
    after = copy.deepcopy(before)
    after['tables'][0]['columns'][0]['name'] = 'contact_email'

    changes = diff_snapshots(before, after)

    assert changes[0]['object_type'] == 'column'
    assert changes[0]['before'] == {'name': 'email'}
    assert changes[0]['after'] == {'name': 'contact_email'}


def test_merge_combines_rename_and_nullability_change() -> None:
    base = {'tables': [table()]}
    source, target = copy.deepcopy(base), copy.deepcopy(base)
    source['tables'][0]['columns'][0]['name'] = 'contact_email'
    target['tables'][0]['columns'][0]['nullable'] = False

    result = merge_snapshots(base, source, target)
    column = result['snapshot']['tables'][0]['columns'][0]

    assert result['conflicts'] == []
    assert column['name'] == 'contact_email'
    assert column['nullable'] is False


def test_rename_rebinds_a_check_and_merges_an_independent_check_change() -> None:
    base = {'tables': [table()]}
    base['tables'][0]['constraints'].append(
        {
            'id': uid(),
            'name': 'email_check',
            'kind': 'check',
            'columns': [base['tables'][0]['columns'][0]['id']],
            'expression': "email <> 'email'",
        }
    )
    base = validate_snapshot(base)
    source, target = copy.deepcopy(base), copy.deepcopy(base)
    source['tables'][0]['columns'][0]['name'] = 'contact_email'
    source = validate_snapshot(rebind_unchanged_checks(base, source))
    assert source['tables'][0]['constraints'][0]['expression'] == "contact_email <> 'email'"
    plan = build_plan(base, source, 'public')
    assert [step['operation']['kind'] for step in plan['steps']] == ['rename_column']
    target['tables'][0]['constraints'][0]['expression'] = "email <> 'blocked'"
    result = merge_snapshots(base, source, target)
    assert result['conflicts'] == []
    assert (
        result['snapshot']['tables'][0]['constraints'][0]['expression']
        == "contact_email <> 'blocked'"
    )


def test_merge_delete_edit_conflict_applies_property_resolution() -> None:
    base = {'tables': [table()]}
    source, target = copy.deepcopy(base), copy.deepcopy(base)
    source['tables'][0]['columns'] = []
    target['tables'][0]['columns'][0]['data_type'] = 'text'

    preview = merge_snapshots(base, source, target)
    conflict = preview['conflicts'][0]
    resolved = merge_snapshots(base, source, target, {conflict['id']: 'source'})

    assert conflict['property'] == 'existence'
    assert resolved['snapshot']['tables'][0]['columns'] == []


def test_merge_reconciles_equivalent_independent_table_additions() -> None:
    base = {'tables': []}
    source, target = {'tables': [table('audit')]}, {'tables': [table('audit')]}
    target_column_id = target['tables'][0]['columns'][0]['id']

    result = merge_snapshots(base, source, target)

    assert result['conflicts'] == []
    assert len(result['snapshot']['tables']) == 1
    assert result['snapshot']['tables'][0]['columns'][0]['id'] == target_column_id


def test_merge_reconciles_equivalent_independent_parent_child_graph() -> None:
    def graph() -> dict:
        parent, child = table('parents'), table('children')
        parent['columns'][0]['nullable'] = False
        parent['constraints'].append(
            {
                'id': uid(),
                'name': 'parents_pkey',
                'kind': 'primary_key',
                'columns': [parent['columns'][0]['id']],
            }
        )
        child['columns'][0]['name'] = 'parent_email'
        child['constraints'].append(
            {
                'id': uid(),
                'name': 'children_parent_fkey',
                'kind': 'foreign_key',
                'columns': [child['columns'][0]['id']],
                'reference_table': parent['id'],
                'reference_columns': [parent['columns'][0]['id']],
            }
        )
        return {'tables': [parent, child]}

    source, target = graph(), graph()

    result = merge_snapshots({'tables': []}, source, target)
    merged_child = next(item for item in result['snapshot']['tables'] if item['name'] == 'children')
    target_parent = next(item for item in target['tables'] if item['name'] == 'parents')

    assert result['conflicts'] == []
    assert merged_child['constraints'][0]['reference_table'] == target_parent['id']


def test_merge_primary_key_nullability_conflict_has_valid_property_resolution() -> None:
    base = {'tables': [table()]}
    base['tables'][0]['columns'][0]['nullable'] = False
    source, target = copy.deepcopy(base), copy.deepcopy(base)
    source['tables'][0]['columns'][0]['nullable'] = True
    target['tables'][0]['constraints'].append(
        {
            'id': uid(),
            'name': 'users_pkey',
            'kind': 'primary_key',
            'columns': [target['tables'][0]['columns'][0]['id']],
        }
    )

    preview = merge_snapshots(base, source, target)
    conflict = next(
        item for item in preview['conflicts'] if item['property'] == 'nullable_for_primary_key'
    )
    source_result = merge_snapshots(base, source, target, {conflict['id']: 'source'})

    assert validate_snapshot(preview['snapshot']) == preview['snapshot']
    assert preview['snapshot']['tables'][0]['columns'][0]['nullable'] is False
    assert source_result['snapshot']['tables'][0]['columns'][0]['nullable'] is True
    assert source_result['snapshot']['tables'][0]['constraints'] == []


def test_merge_type_default_conflict_keeps_a_valid_target_fallback() -> None:
    base = {'tables': [table()]}
    base['tables'][0]['columns'][0]['data_type'] = 'text'
    source, target = copy.deepcopy(base), copy.deepcopy(base)
    source['tables'][0]['columns'][0]['default'] = "'not a number'"
    target['tables'][0]['columns'][0]['data_type'] = 'integer'

    preview = merge_snapshots(base, source, target)
    conflict = next(
        item for item in preview['conflicts'] if item['property'] == 'default_for_data_type'
    )
    source_result = merge_snapshots(base, source, target, {conflict['id']: 'source'})

    assert preview['snapshot']['tables'][0]['columns'][0]['default'] is None
    assert source_result['snapshot']['tables'][0]['columns'][0]['data_type'] == 'text'
    assert source_result['snapshot']['tables'][0]['columns'][0]['default'] == "'not a number'"


def test_merge_dependency_conflict_can_restore_referenced_table() -> None:
    parent, child = table('parents'), table('children')
    parent['columns'][0]['nullable'] = False
    parent['constraints'].append(
        {
            'id': uid(),
            'name': 'parents_pkey',
            'kind': 'primary_key',
            'columns': [parent['columns'][0]['id']],
        }
    )
    base = {'tables': [parent, child]}
    source, target = copy.deepcopy(base), copy.deepcopy(base)
    source['tables'][1]['constraints'].append(
        {
            'id': uid(),
            'name': 'children_parent_fkey',
            'kind': 'foreign_key',
            'columns': [child['columns'][0]['id']],
            'reference_table': parent['id'],
            'reference_columns': [parent['columns'][0]['id']],
        }
    )
    target['tables'] = [target['tables'][1]]

    preview = merge_snapshots(base, source, target)
    conflict = next(item for item in preview['conflicts'] if item['property'] == 'dependencies')
    resolved = merge_snapshots(base, source, target, {conflict['id']: 'source'})

    assert {item['name'] for item in resolved['snapshot']['tables']} == {'parents', 'children'}
    merged_child = next(
        item for item in resolved['snapshot']['tables'] if item['name'] == 'children'
    )
    assert merged_child['constraints'][0]['name'] == 'children_parent_fkey'


def test_merge_dependency_conflict_can_restore_referenced_unique_key() -> None:
    parent, child = table('parents'), table('children')
    parent['constraints'].append(
        {
            'id': uid(),
            'name': 'parents_email_key',
            'kind': 'unique',
            'columns': [parent['columns'][0]['id']],
        }
    )
    base = {'tables': [parent, child]}
    source, target = copy.deepcopy(base), copy.deepcopy(base)
    source['tables'][1]['constraints'].append(
        {
            'id': uid(),
            'name': 'children_parent_fkey',
            'kind': 'foreign_key',
            'columns': [child['columns'][0]['id']],
            'reference_table': parent['id'],
            'reference_columns': [parent['columns'][0]['id']],
        }
    )
    target['tables'][0]['constraints'] = []

    preview = merge_snapshots(base, source, target)
    conflict = next(item for item in preview['conflicts'] if item['property'] == 'reference_key')
    resolved = merge_snapshots(base, source, target, {conflict['id']: 'source'})
    merged_parent = next(
        item for item in resolved['snapshot']['tables'] if item['name'] == 'parents'
    )

    assert validate_snapshot(resolved['snapshot']) == resolved['snapshot']
    assert merged_parent['constraints'][0]['name'] == 'parents_email_key'


def test_online_plan_stages_not_null_check_and_concurrent_index() -> None:
    before = {'tables': [table()]}
    after = copy.deepcopy(before)
    item = after['tables'][0]
    item['columns'][0]['nullable'] = False
    item['indexes'].append(
        {
            'id': uid(),
            'name': 'users_email_idx',
            'columns': [item['columns'][0]['id']],
            'unique': False,
        }
    )
    item['constraints'].append(
        {
            'id': uid(),
            'name': 'users_email_check',
            'kind': 'check',
            'columns': [],
            'expression': 'email IS NOT NULL',
        }
    )

    plan = build_plan(before, after, 'app', online=True)
    statements = [step['sql'] for step in plan['steps']]
    index_step = next(step for step in plan['steps'] if step['operation']['kind'] == 'create_index')

    assert any('CHECK ("email" IS NOT NULL) NOT VALID' in statement for statement in statements)
    assert any('VALIDATE CONSTRAINT "users_email_check"' in statement for statement in statements)
    assert any(
        'CREATE INDEX CONCURRENTLY "users_email_idx" ON "app"."users"' in statement
        for statement in statements
    )
    assert index_step['transactional'] is False
    assert index_step['operation'] == {
        'kind': 'create_index',
        'schema': 'app',
        'name': 'users_email_idx',
        'table': 'users',
        'unique': False,
        'columns': ['email'],
    }


def test_planner_rejects_unsupported_type_conversion() -> None:
    before = {'tables': [table()]}
    before['tables'][0]['columns'][0]['data_type'] = 'text'
    after = copy.deepcopy(before)
    after['tables'][0]['columns'][0]['data_type'] = 'boolean'

    with pytest.raises(ValueError, match='unsupported type conversion'):
        build_plan(before, after, 'app')


def test_drop_index_operation_uses_before_definition_before_renames() -> None:
    before = {'tables': [table()]}
    before['tables'][0]['indexes'].append(
        {
            'id': uid(),
            'name': 'users_email_idx',
            'columns': [before['tables'][0]['columns'][0]['id']],
            'unique': True,
        }
    )
    after = copy.deepcopy(before)
    after['tables'][0]['name'] = 'people'
    after['tables'][0]['columns'][0]['name'] = 'contact_email'
    after['tables'][0]['indexes'] = []

    plan = build_plan(before, after, 'app')
    step = next(item for item in plan['steps'] if item['operation']['kind'] == 'drop_index')

    assert step['operation'] == {
        'kind': 'drop_index',
        'schema': 'app',
        'name': 'users_email_idx',
        'table': 'users',
        'unique': True,
        'columns': ['email'],
    }
    assert plan['steps'].index(step) < next(
        index
        for index, item in enumerate(plan['steps'])
        if item['operation']['kind'] == 'rename_table'
    )


def test_validation_rejects_global_relation_collisions_and_invalid_foreign_keys() -> None:
    left, right = table('left_table'), table('right_table')
    left['indexes'].append(
        {
            'id': uid(),
            'name': 'shared_relation',
            'columns': [left['columns'][0]['id']],
            'unique': False,
        }
    )
    right['indexes'].append(
        {
            'id': uid(),
            'name': 'shared_relation',
            'columns': [right['columns'][0]['id']],
            'unique': False,
        }
    )

    with pytest.raises(ValueError, match='duplicates a relation name'):
        validate_snapshot({'tables': [left, right]})

    parent, child = table('parent'), table('child')
    child['constraints'].append(
        {
            'id': uid(),
            'name': 'child_parent_fkey',
            'kind': 'foreign_key',
            'columns': [child['columns'][0]['id']],
            'reference_table': parent['id'],
            'reference_columns': [parent['columns'][0]['id']],
        }
    )

    with pytest.raises(ValueError, match='primary key or unique key'):
        validate_snapshot({'tables': [parent, child]})


def test_validation_rejects_invalid_identity_definition() -> None:
    snapshot = {'tables': [table()]}
    snapshot['tables'][0]['columns'][0]['identity'] = 'always'

    with pytest.raises(ValueError, match='identity columns'):
        validate_snapshot(snapshot)


def test_plan_creates_referenced_key_before_foreign_key() -> None:
    parent, child = table('parents'), table('children')
    parent['columns'][0]['nullable'] = False
    parent['constraints'].append(
        {
            'id': uid(),
            'name': 'parents_pkey',
            'kind': 'primary_key',
            'columns': [parent['columns'][0]['id']],
        }
    )
    child['columns'][0]['name'] = 'parent_email'
    child['constraints'].append(
        {
            'id': uid(),
            'name': 'children_parent_email_fkey',
            'kind': 'foreign_key',
            'columns': [child['columns'][0]['id']],
            'reference_table': parent['id'],
            'reference_columns': [parent['columns'][0]['id']],
        }
    )

    plan = build_plan({'tables': []}, {'tables': [child, parent]}, 'app')
    statements = [step['sql'] for step in plan['steps']]
    key = next(
        index
        for index, statement in enumerate(statements)
        if 'ADD CONSTRAINT "parents_pkey" PRIMARY KEY' in statement
    )
    foreign_key = next(
        index
        for index, statement in enumerate(statements)
        if 'ADD CONSTRAINT "children_parent_email_fkey" FOREIGN KEY' in statement
    )

    assert key < foreign_key


def test_plan_drops_and_readds_foreign_key_dependencies_for_retypes() -> None:
    parent, child = table('parents'), table('children')
    parent['columns'][0].update({'name': 'id', 'data_type': 'integer', 'nullable': False})
    child['columns'][0].update({'name': 'parent_id', 'data_type': 'integer'})
    parent['constraints'].append(
        {
            'id': uid(),
            'name': 'parents_pkey',
            'kind': 'primary_key',
            'columns': [parent['columns'][0]['id']],
        }
    )
    child['constraints'].append(
        {
            'id': uid(),
            'name': 'children_parent_fkey',
            'kind': 'foreign_key',
            'columns': [child['columns'][0]['id']],
            'reference_table': parent['id'],
            'reference_columns': [parent['columns'][0]['id']],
        }
    )
    before = {'tables': [parent, child]}
    after = copy.deepcopy(before)
    for item in after['tables']:
        item['columns'][0]['data_type'] = 'bigint'

    plan = build_plan(before, after, 'app')
    steps = plan['steps']
    drop_fk = next(
        index
        for index, step in enumerate(steps)
        if step['operation'].get('name') == 'children_parent_fkey'
        and step['operation']['kind'] == 'drop_constraint'
    )
    drop_key = next(
        index
        for index, step in enumerate(steps)
        if step['operation'].get('name') == 'parents_pkey'
        and step['operation']['kind'] == 'drop_constraint'
    )
    retype = next(
        index for index, step in enumerate(steps) if step['operation']['kind'] == 'retype_column'
    )
    add_key = next(
        index
        for index, step in enumerate(steps)
        if step['operation'].get('name') == 'parents_pkey'
        and step['operation']['kind'] == 'add_constraint'
    )
    add_fk = next(
        index
        for index, step in enumerate(steps)
        if step['operation'].get('name') == 'children_parent_fkey'
        and step['operation']['kind'] == 'add_constraint'
    )

    assert drop_fk < drop_key < retype < add_key < add_fk

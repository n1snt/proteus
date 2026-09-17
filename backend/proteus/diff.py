"""Semantic snapshot comparison."""

from __future__ import annotations

import hashlib
from typing import Any

from .schema import validate_snapshot


def _change(
    kind: str,
    object_type: str,
    table_name: str | None,
    object_name: str,
    summary: str,
    before: Any,
    after: Any,
) -> dict[str, Any]:
    identity = f'{object_type}:{table_name or ""}:{object_name}:{kind}:{summary}'
    return {
        'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
        'kind': kind,
        'object_type': object_type,
        'table_name': table_name,
        'object_name': object_name,
        'summary': summary,
        'before': before,
        'after': after,
    }


def _by_id(values: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {value['id']: value for value in values}


def _diff_children(
    before: dict[str, Any],
    after: dict[str, Any],
    key: str,
    object_type: str,
    changes: list[dict[str, Any]],
) -> None:
    old_items = _by_id(before[key])
    new_items = _by_id(after[key])
    for item_id in sorted(old_items.keys() - new_items.keys()):
        item = old_items[item_id]
        changes.append(
            _change(
                'removed',
                object_type,
                before['name'],
                item['name'],
                f'Removed {object_type.replace("_", " ")} {item["name"]}',
                item,
                None,
            )
        )
    for item_id in sorted(new_items.keys() - old_items.keys()):
        item = new_items[item_id]
        changes.append(
            _change(
                'added',
                object_type,
                after['name'],
                item['name'],
                f'Added {object_type.replace("_", " ")} {item["name"]}',
                None,
                item,
            )
        )
    for item_id in sorted(old_items.keys() & new_items.keys()):
        old, new = old_items[item_id], new_items[item_id]
        for property_name in sorted(set(old) | set(new)):
            if property_name != 'id' and old.get(property_name) != new.get(property_name):
                name = new['name']
                changes.append(
                    _change(
                        'modified',
                        object_type,
                        after['name'],
                        name,
                        f'Changed {object_type.replace("_", " ")} {name}: {property_name.replace("_", " ")}',
                        {property_name: old.get(property_name)},
                        {property_name: new.get(property_name)},
                    )
                )


def diff_snapshots(before: Any, after: Any) -> list[dict[str, Any]]:
    """Compare validated snapshots by stable object identity and properties."""
    old_snapshot, new_snapshot = validate_snapshot(before), validate_snapshot(after)
    old_tables, new_tables = _by_id(old_snapshot['tables']), _by_id(new_snapshot['tables'])
    changes: list[dict[str, Any]] = []
    for table_id in sorted(old_tables.keys() - new_tables.keys()):
        table = old_tables[table_id]
        changes.append(
            _change(
                'removed',
                'table',
                table['name'],
                table['name'],
                f'Removed table {table["name"]}',
                table,
                None,
            )
        )
    for table_id in sorted(new_tables.keys() - old_tables.keys()):
        table = new_tables[table_id]
        changes.append(
            _change(
                'added',
                'table',
                table['name'],
                table['name'],
                f'Added table {table["name"]}',
                None,
                table,
            )
        )
    for table_id in sorted(old_tables.keys() & new_tables.keys()):
        old, new = old_tables[table_id], new_tables[table_id]
        if old['name'] != new['name']:
            changes.append(
                _change(
                    'modified',
                    'table',
                    new['name'],
                    new['name'],
                    f'Renamed table {old["name"]} to {new["name"]}',
                    {'name': old['name']},
                    {'name': new['name']},
                )
            )
        _diff_children(old, new, 'columns', 'column', changes)
        _diff_children(old, new, 'constraints', 'constraint', changes)
        _diff_children(old, new, 'indexes', 'index', changes)
    return changes
